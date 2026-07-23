"""
RUNECLAW — Web User Gateway
===========================
aiohttp sub-app (mounted at /gateway by dashboard_server.create_app) that lets
the website (app/ Express platform) talk to the LIVE bot process:

  POST /gateway/chat           — chatbot on the web (all authorized users)
  GET  /gateway/chat/history   — hydrate the web chat drawer
  GET  /gateway/portfolio      — caller's own PAPER portfolio snapshot
  POST /gateway/trade/propose  — manual trade -> pending idea (same as /trade)
  POST /gateway/trade/confirm  — engine.confirm_trade (THE single execution path)
  POST /gateway/trade/cancel   — withdraw own pending manual idea

Trust model: requests come ONLY from the Express server (app/routes/*.js),
which authenticates the browser user with JWT and injects the identity
server-side — the linked telegram_id, or "web:<user_id>" for web-only
accounts (auto-provisioned here as paper-only traders; structurally unable
to trade live). This gateway re-authenticates the service channel with
X-Gateway-Secret (WEB_GATEWAY_SECRET, >=32 chars, constant-time compare,
fail-closed 403 when unset) and re-authorizes the USER on every call against
the bot's own UserStore + allowlist + live gate — the website can never grant
live access the operator hasn't already granted.

No new execution code: trades ride the exact Telegram path
(parse_manual_trade -> build_manual_idea -> engine._pending_ideas ->
engine.confirm_trade), so every risk gate, drift check, and per-symbol lock
applies identically.
"""

from __future__ import annotations

import hmac
import html as _html
import math
import os
import re
import time
from datetime import datetime, timezone

from aiohttp import web

from bot.config import CONFIG
from bot.utils.logger import audit, system_log

# Fail-closed: gateway refuses all requests unless the operator configured a
# strong shared secret on both sides (bot + Express).
_GATEWAY_SECRET: str = os.environ.get("WEB_GATEWAY_SECRET", "")
_MIN_SECRET_LEN = 32

_MAX_TEXT_LEN = 2000
_MAX_PROPOSERS = 500  # bound the proposer map (pending ideas expire anyway)


def _secret() -> str:
    # Read the environment on every request (not only import time) so a vault
    # restore or an admin /setgateway repair takes effect WITHOUT a restart.
    # Falls back to the import-time value so tests can monkeypatch module state.
    return os.environ.get("WEB_GATEWAY_SECRET", "") or _GATEWAY_SECRET


@web.middleware
async def secret_middleware(request: web.Request, handler):
    secret = _secret()
    if not secret or len(secret) < _MIN_SECRET_LEN:
        return web.json_response(
            {"error": "gateway_disabled",
             "detail": "WEB_GATEWAY_SECRET not configured (>=32 chars required)."},
            status=403)
    provided = request.headers.get("X-Gateway-Secret", "")
    if not provided or not hmac.compare_digest(provided, secret):
        return web.json_response({"error": "forbidden"}, status=403)
    return await handler(request)


# ── User authorization (mirrors TelegramHandler._guard, sans Update) ────────

# Web-only identities: "web:<website user id>". Provisioned automatically on
# first gateway request (paper-only trader), because the caller is already
# authenticated by the Express server's JWT layer. Real Telegram ids are
# numeric, and MultiUserPortfolio's key sanitizer strips ":" (web:5 -> web5),
# so these can never collide with a Telegram user's records.
_WEB_ID_RE = re.compile(r"^web:\d{1,20}$")


def _is_web_id(tg_id: str) -> bool:
    return bool(_WEB_ID_RE.match(tg_id))


def _is_admin_id(tg_handler, tg_id: str) -> bool:
    """TelegramHandler._is_admin semantics keyed by raw telegram id."""
    user = tg_handler.users.get(tg_id)
    if user is not None and user.get("role") == "admin":
        return True
    raw = CONFIG.telegram.admin_ids
    if raw:
        return tg_id in {s.strip() for s in str(raw).split(",") if s.strip()}
    return False


def _guard_user(tg_handler, tg_id: str, command: str = "", name: str = ""):
    """Auth + role + rate limit for a web-originated request.

    Returns None when allowed, or a web.json_response error. Mirrors
    TelegramHandler._guard: allowlist -> registered+authorized -> role
    permission -> rate limit.

    Web-only identities ("web:<id>"): the Telegram allowlist does not apply
    (the Express JWT layer already authenticated the caller, and web ids are
    structurally locked out of live trading — see _can_trade_live guards);
    unknown web ids are auto-provisioned as paper-only traders via
    UserStore.register. Telegram-shaped ids keep the exact prior semantics.
    """
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if tg_id.startswith("web:") and not _is_web_id(tg_id):
        return web.json_response({"error": "invalid_web_id"}, status=400)
    if _is_web_id(tg_id):
        # Auto-provision (or refresh last_seen for the 24h staleness check).
        # register() never overwrites role/tier on existing users.
        tg_handler.users.register(tg_id, name=name)
    else:
        allow = tg_handler._allowlist_ids()
        if allow and tg_id not in allow:
            return web.json_response(
                {"error": "not_allowlisted",
                 "detail": "This bot is locked to its configured operator."},
                status=403)
    user = tg_handler.users.get(tg_id)
    if not user or not user.get("authorized", False):
        return web.json_response(
            {"error": "not_authorized",
             "detail": "Not registered/approved on the bot. Use /start in Telegram."},
            status=403)
    if command and not tg_handler.users.has_permission(tg_id, command):
        return web.json_response(
            {"error": "no_permission",
             "detail": f"Your role cannot use {command}."},
            status=403)
    if not tg_handler._limiter.allow(f"web:{tg_id}"):
        return web.json_response({"error": "rate_limited"}, status=429)
    return None


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


# ── Chat (all authorized users; LLM tier follows the caller's real role) ────

_PROFILE_RISK_PREFS = frozenset({"conservative", "balanced", "aggressive"})
_PROFILE_WATCHLIST_MAX = 20
_PROFILE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,20}$")


def build_profile_note(profile) -> str:
    """Compact, whitelisted context line from the web user's saved profile.

    The Express server already validates profile writes, but this re-filters
    (defense-in-depth) because the note lands in the LLM system prompt:
    risk_pref must be one of three known words and watchlist symbols must be
    bare uppercase tickers — nothing free-form can ride through here.
    """
    if not isinstance(profile, dict):
        return ""
    parts = []
    risk = str(profile.get("risk_pref") or "").lower()
    if risk in _PROFILE_RISK_PREFS:
        parts.append(f"Their self-declared risk preference is {risk}.")
    wl = profile.get("watchlist")
    if isinstance(wl, list):
        syms = [s for s in (str(x or "").upper() for x in wl[:_PROFILE_WATCHLIST_MAX])
                if _PROFILE_SYMBOL_RE.match(s)]
        if syms:
            parts.append("They are watching: " + ", ".join(syms) + ".")
    return " ".join(parts)


async def handle_chat(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    text = str(body.get("text") or "").strip()
    name = str(body.get("name") or "").strip()[:64]
    profile_note = build_profile_note(body.get("profile"))
    reply_lang = str(body.get("lang") or "").strip()[:12]

    # WEB-VISION: optional image attachments (chart / positions screenshots).
    # Admin/ULTRA-gated downstream in _llm_chat (is_admin and not public), so a
    # non-admin's images are simply ignored there — the text still answers.
    images = None
    _raw_imgs = body.get("images")
    if isinstance(_raw_imgs, list) and _raw_imgs:
        images = []
        for _it in _raw_imgs[:4]:
            if isinstance(_it, dict) and _it.get("data"):
                images.append({
                    "media_type": str(_it.get("media_type") or "image/png")[:40],
                    "data": str(_it["data"]),
                })
        images = images or None

    if not tg_id or (not text and not images):
        return web.json_response({"error": "telegram_id and text (or image) required"}, status=400)
    if len(text) > _MAX_TEXT_LEN:
        return web.json_response({"error": "message too long"}, status=400)

    err = _guard_user(tg_handler, tg_id, name=name)
    if err is not None:
        return err

    # Guardian firewall pre-scan — the web chat can ACT (propose trades,
    # dispatch skills) exactly like Telegram, so the same input-provenance gate
    # applies here. Telemetry-first + fail-open: the engine records a FIREWALL
    # verdict to the tamper-evident chain and returns it; a message is only
    # refused when the operator has opted into blocking HIGH verdicts. Default
    # OFF (no scan) — this can never break a chat.
    try:
        fw_verdict = engine.firewall_scan(text, source="web", user_id=tg_id)
        if fw_verdict and fw_verdict.get("risk") == "high" \
                and bool(getattr(CONFIG.risk, "guardian_firewall_block_high", False)):
            _cats = ", ".join(fw_verdict.get("categories", [])[:3]) or "manipulation"
            return web.json_response({
                "reply_html": (
                    "🛡️ <b>Blocked by the Guardian firewall.</b><br><br>"
                    "That message looked like a prompt-injection / unsafe-action "
                    f"attempt (<i>{_html.escape(_cats)}</i>), so I won't act on it. "
                    "Rephrase what you actually want and I'll help."),
                "intent": "firewall_blocked"})
    except Exception:
        pass

    # WEB-VISION: an image message goes straight to the vision-capable LLM path
    # and skips the trade / skill intercepts (which key off text commands), so a
    # pasted screenshot is always READ, never parsed as a command. Admin/ULTRA
    # only — it rides the operator's Claude key, exactly like the Telegram photo
    # handler; a non-admin gets a friendly note and no LLM spend.
    if images:
        if not _is_admin_id(tg_handler, tg_id):
            return web.json_response({
                "reply_html": "📷 Image analysis is available to the operator only.",
                "intent": "vision_denied"})
        from bot.nlp.sanitize import sanitize_chat_input as _san_v
        _q = text or (
            "Read this trading screenshot. If it's a chart, describe the "
            "structure, trend, key levels and any setup or risk you see. If "
            "it's a positions / PnL screen, summarise the positions, exposure "
            "and risks. Be concise and specific; note anything that looks off.")
        tg_handler.conversations.append(
            tg_id, "user", text or "[image]",
            metadata={"intent": "vision", "surface": "web"})
        answer, meta = await tg_handler._llm_chat(
            _san_v(_q), user_id=tg_id, user_name=name,
            is_admin=True, profile_note=profile_note, reply_lang=reply_lang,
            return_meta=True, images=images)
        tg_handler.conversations.append(tg_id, "assistant", answer,
                                        metadata={"surface": "web"})
        return web.json_response({"reply_html": answer, "intent": "vision",
                                  "model": (meta or {}).get("model", ""),
                                  "provider": (meta or {}).get("provider", "")})

    # Manual trade via natural language — same intercept as _handle_message:
    # "buy SOL 71 sl 70 tp 76" proposes a pending trade (never executes).
    trade_text = text.lower().strip()
    if trade_text.startswith("trade "):
        trade_text = trade_text[6:].strip()
    if (any(trade_text.startswith(p) for p in ("buy ", "long ", "short ", "sell "))
            and " sl " in trade_text):
        return _propose_from_text(request.app, tg_handler, engine, tg_id,
                                  trade_text, name=name)

    # Bare directional ask — "long ETH" / "paper short sol" (no explicit
    # levels). NEVER loosened into an order: it routes to analyze_asset, so
    # the user gets the agent's actual setup (entry/SL/TP from the engine)
    # with the one-tap "Trade this" card. SL discipline stays mandatory —
    # only the strict "buy X <entry> sl <sl> tp <tp>" form proposes directly.
    _bare = re.match(r"^(?:paper\s+)?(?:long|short|buy|sell)\s+([a-z0-9]{2,12})$",
                     trade_text)
    if _bare:
        skill = tg_handler.registry.get("analyze_asset")
        if skill:
            _sym = _bare.group(1).upper()
            tg_handler.conversations.append(tg_id, "user", text,
                                            metadata={"intent": "analyze_asset",
                                                      "surface": "web"})
            ideas_before = set(getattr(engine, "_pending_ideas", {}) or {})
            try:
                result = await skill.execute(engine, user_id=tg_id, symbol=_sym)
            except Exception:
                return web.json_response(
                    {"reply_html": "Couldn't analyze that right now — try again.",
                     "intent": "analyze_asset"}, status=200)
            resp = {"reply_html": result, "intent": "analyze_asset"}
            setup = _setup_from_new_idea(engine, ideas_before)
            if setup is not None:
                resp["setup"] = setup
            return web.json_response(resp)

    # Intent routing — same threshold as Telegram (confidence >= 0.8).
    intent = tg_handler.intent_router.classify_rules(text)
    # Stance intents are Telegram-flow only (a confirm-button proposal for
    # the OPERATOR's global mode). On the web, answer honestly instead of
    # silently falling through to generic LLM chat.
    if intent.matched and intent.confidence >= 0.8 \
            and intent.skill.startswith("stance_"):
        _want = intent.skill.removeprefix("stance_")
        return web.json_response({
            "reply_html": (
                f"Your <b>personal risk preference</b> lives on the Home view "
                f"(the 🛡/⚖️/🔥 chips under <i>Your agent</i>) — set it to "
                f"<b>{_html.escape(_want)}</b> there and I'll tailor how I talk "
                f"to you. The engine's <b>global</b> stance is an operator "
                f"control (admins: the stance buttons on Home, or /agent in "
                f"Telegram)."),
            "intent": intent.skill})
    if intent.matched and intent.confidence >= 0.8:
        # Router intents whose skills exist only as Telegram command handlers:
        # map them to the closest registered skill so a web ask ACTS instead
        # of degrading to generic chat.
        _INTENT_ALIASES = {
            "scan_swing": "scan_market", "scan_scalp": "scan_market",
            "scan_intraday": "scan_market", "scan_deep": "scan_market",
            "scan_full": "scan_market",
            "status": "get_portfolio", "get_orders": "get_portfolio",
        }
        skill_name = _INTENT_ALIASES.get(intent.skill, intent.skill)
        skill = tg_handler.registry.get(skill_name)
        if skill:
            audit(system_log, f"Web NL intent routed: '{text[:50]}' -> {intent.skill}",
                  action="web_intent_dispatch", result=intent.skill,
                  data={"confidence": intent.confidence, "source": intent.source})
            tg_handler.conversations.append(tg_id, "user", text,
                                            metadata={"intent": intent.skill,
                                                      "surface": "web"})
            # Snapshot queued ideas so an analysis that produces a concrete
            # setup (analyze_asset registers one in engine._pending_ideas) can
            # be offered to the web as a one-tap "Trade this".
            ideas_before = set(getattr(engine, "_pending_ideas", {}) or {})
            try:
                result = await skill.execute(engine, user_id=tg_id, **intent.kwargs)
            except Exception:
                return web.json_response(
                    {"reply_html": "Something went wrong. Try again or use a command.",
                     "intent": intent.skill}, status=200)
            tg_handler.conversations.append(
                tg_id, "assistant", f"[{intent.skill}] executed successfully",
                metadata={"skill": intent.skill, "surface": "web"})
            resp = {"reply_html": result, "intent": intent.skill}
            setup = _setup_from_new_idea(engine, ideas_before)
            if setup is not None:
                resp["setup"] = setup
            return web.json_response(resp)

    # News radar intercept — "news"/"headlines" as free text must hit the real
    # RSS radar, not the tool-less chat LLM (which denies having a feed). Same
    # shared detector + digest helper the Telegram surface uses, so both behave
    # identically.
    from bot.core.news import looks_like_news_request
    if looks_like_news_request(text):
        tg_handler.conversations.append(
            tg_id, "assistant", "[news] radar digest",
            metadata={"skill": "news", "surface": "web"})
        return web.json_response(
            {"reply_html": await tg_handler._news_digest_text(), "intent": "news"})

    # Fallback: LLM chat — same append-around-call pattern as _handle_message.
    # Free-tier chat quota: bound the operator-funded xAI Grok budget. Only the
    # LLM fallback (this path) consumes a "question" — skill/news/trade intents
    # above are free. Admin + paid tiers (pro/elite) are exempt; a free user gets
    # N questions/day (default 5) then an upgrade prompt instead of an LLM call.
    _is_admin = _is_admin_id(tg_handler, tg_id)
    try:
        _tier = "admin" if _is_admin else tg_handler.users.get_tier(tg_id)
    except Exception:
        _tier = "admin" if _is_admin else "basic"
    from bot.web import chat_quota
    _q = chat_quota.consume(tg_id, _tier)
    if not _q.get("allowed"):
        _lim = _q.get("limit") or chat_quota.free_daily_limit()
        # Tell the capped user WHEN their free questions return, so the wall
        # reads as a wait, not a dead end. Humanise the reset window.
        _secs = _q.get("reset_in_seconds")
        if isinstance(_secs, (int, float)) and _secs > 0:
            _hrs = int(_secs) // 3600
            if _hrs >= 2:
                _reset = f"Your free questions reset in about {_hrs} hours"
            elif _hrs == 1:
                _reset = "Your free questions reset in about an hour"
            else:
                _reset = "Your free questions reset within the hour"
        else:
            _reset = "Your free questions reset tomorrow"
        return web.json_response({
            "reply_html": (
                f"🚀 <b>You've used your {_lim} free questions for today.</b><br><br>"
                "Upgrade to keep chatting with the agent — unlimited questions plus "
                f"priority models, live signals, and deeper research. {_reset}.<br><br>"
                "<a href=\"/dashboard#account\">See plans →</a>"),
            "intent": "quota_exceeded", "quota": _q}, status=200)

    from bot.nlp.sanitize import sanitize_chat_input
    tg_handler.conversations.append(tg_id, "user", text,
                                    metadata={"intent": intent.skill or "chat",
                                              "surface": "web"})
    # is_admin MUST reflect the caller's real role: resolve_tier_config's
    # non-admin guard (operator Anthropic key stays admin-only) and the
    # fallback-chain gate in _llm_chat both key off this flag.
    answer, meta = await tg_handler._llm_chat(
        sanitize_chat_input(text), user_id=tg_id, user_name=name,
        is_admin=_is_admin,
        profile_note=profile_note, reply_lang=reply_lang, return_meta=True)
    tg_handler.conversations.append(tg_id, "assistant", answer,
                                    metadata={"surface": "web"})
    # Model transparency: the web renders a small caption showing WHICH
    # model answered — the visible face of tier routing (and of a runeclaw
    # promotion via /settier). `quota` lets the UI show "N left today".
    return web.json_response({"reply_html": answer, "intent": "chat",
                              "model": (meta or {}).get("model", ""),
                              "provider": (meta or {}).get("provider", ""),
                              "quota": _q})


# ── Public chat (anonymous website visitors; market Q&A only) ───────────────
#
# SAFETY BOUNDARY. This path is intentionally account-free and is the ONLY
# gateway endpoint reachable without a resolved user identity. It:
#   * NEVER calls _guard_user / users.register  → no user is provisioned;
#   * NEVER runs the manual-trade intercept       → no pending trade is created;
#   * NEVER dispatches an intent/skill            → no account/portfolio/order
#                                                    data and no engine action;
#   * only asks the LLM via _llm_chat(public=True), which uses a STATIC
#     market-only system prompt with no portfolio context, no conversation
#     history, and no admin-only provider.
# So no account data or trade action is reachable from this endpoint regardless
# of what the anonymous client sends. Skills/quick-actions for signed-in users
# stay on handle_chat. The Express side (routes/public_chat.js) rate-limits per
# IP; the LLM daily-budget guard inside _llm_chat bounds spend.

async def handle_public_chat(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    text = str(body.get("text") or "").strip()

    if not text:
        return web.json_response({"error": "text required"}, status=400)
    if len(text) > _MAX_TEXT_LEN:
        return web.json_response({"error": "message too long"}, status=400)

    reply_lang = str(body.get("lang") or "").strip()[:12]
    from bot.nlp.sanitize import sanitize_chat_input
    answer = await tg_handler._llm_chat(
        sanitize_chat_input(text), user_id="", user_name="",
        is_admin=False, public=True, reply_lang=reply_lang)
    return web.json_response({"reply_html": answer, "intent": "chat"})


async def handle_contract_studio(request: web.Request) -> web.Response:
    """AI smart-contract drafting: an NL spec → a Solidity DRAFT + heuristic
    security flags. Tier-routed LLM (admins/paid get the priority model); free
    users spend from the same daily quota as chat. The output is a DRAFT with
    FLAGS, NEVER an audit or a safety verdict — the disclaimer travels with every
    response (§4). No money-path: this generates + reviews text only."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    spec = str(body.get("spec") or body.get("text") or "").strip()
    name = str(body.get("name") or "").strip()[:64]
    lic = (str(body.get("license") or "MIT").strip()[:40] or "MIT")
    pragma = (str(body.get("pragma") or "0.8.24").strip()[:16] or "0.8.24")

    if not tg_id or not spec:
        return web.json_response({"error": "telegram_id and spec required"}, status=400)
    if len(spec) > _MAX_TEXT_LEN:
        return web.json_response({"error": "spec too long"}, status=400)

    err = _guard_user(tg_handler, tg_id, name=name)
    if err is not None:
        return err

    _is_admin = _is_admin_id(tg_handler, tg_id)
    try:
        _tier = "admin" if _is_admin else tg_handler.users.get_tier(tg_id)
    except Exception:
        _tier = "admin" if _is_admin else "basic"
    from bot.web import chat_quota
    _q = chat_quota.consume(tg_id, _tier)
    if not _q.get("allowed"):
        _lim = _q.get("limit") or chat_quota.free_daily_limit()
        return web.json_response({
            "reply_html": (
                f"🚀 <b>You've used your {_lim} free contract drafts for today.</b>"
                "<br><br>Upgrade for unlimited Contract Studio drafts on the "
                "priority model, plus the security-flag pass and testnet deploy."
                "<br><br><a href=\"/dashboard#account/aplan\">See plans →</a>"),
            "intent": "quota_exceeded", "quota": _q}, status=200)

    from bot.core.contract_studio import (
        build_generation_prompt, scan_security_flags, flags_summary,
        AUDIT_DISCLAIMER)
    from bot.nlp.sanitize import sanitize_chat_input
    prompt = build_generation_prompt(sanitize_chat_input(spec), license=lic,
                                     pragma=pragma)
    answer, meta = await tg_handler._llm_chat(
        prompt, user_id=tg_id, user_name=name, is_admin=_is_admin,
        profile_note="", reply_lang="", return_meta=True)

    # Heuristic security pass over the model's own output — flags to review,
    # never a verdict. Serialised for the client.
    import dataclasses
    flags = scan_security_flags(answer or "")
    return web.json_response({
        "solidity": answer,
        "flags": [dataclasses.asdict(f) for f in flags],
        "summary": flags_summary(flags),
        "disclaimer": AUDIT_DISCLAIMER,
        "model": (meta or {}).get("model", ""),
        "provider": (meta or {}).get("provider", ""),
        "intent": "contract_studio",
        "quota": _q,
    })


async def handle_contract_compile(request: web.Request) -> web.Response:
    """Compile a Solidity draft and report whether it BUILDS, with its bytecode +
    ABI and any solc diagnostics. The prerequisite for the (separate, gated)
    testnet-deploy slice — you cannot deploy a draft that will not compile.

    Compilation is PURE computation — it signs nothing and moves no value — but
    solc is blocking + CPU-heavy, so it runs off the event loop. Fail-soft: if
    the operator hasn't installed the optional compiler, this returns a clear
    'compiler not available' rather than an error. Compiling ≠ safe: the audit
    disclaimer still travels with the result (§4)."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    name = str(body.get("name") or "").strip()[:64]
    source = str(body.get("solidity") or body.get("source") or "").strip()
    optimize = bool(body.get("optimize", True))

    if not tg_id or not source:
        return web.json_response({"error": "telegram_id and solidity required"}, status=400)
    if len(source) > _MAX_TEXT_LEN:
        return web.json_response({"error": "source too long"}, status=400)

    err = _guard_user(tg_handler, tg_id, name=name)
    if err is not None:
        return err

    import asyncio as _asyncio
    from bot.core.contract_studio import (
        compile_source, summarize_compile, AUDIT_DISCLAIMER)
    result = await _asyncio.to_thread(compile_source, source, optimize=optimize)
    return web.json_response({
        "ok": bool(result.get("ok")),
        "available": bool(result.get("available")),
        "compile_error": result.get("error"),
        "diagnostics": result.get("diagnostics") or [],
        "contracts": result.get("contracts") or [],
        "summary": summarize_compile(result),
        "disclaimer": AUDIT_DISCLAIMER,
        "intent": "contract_compile",
    })


async def handle_contract_deploy(request: web.Request) -> web.Response:
    """Contract Studio slice 5 — admin-only, TESTNET-ONLY one-click deploy of a
    compiled contract's init bytecode. This is a contract-CREATION sign+broadcast
    (``to`` omitted, ``data`` = bytecode), run through the SAME fail-closed spine
    as the value-transfer signer: triple-gated default-OFF (feature + signing +
    key + eth-account library + an enforcing envelope), authorized through the
    Authority Envelope as a ``deploy``, mainnet refused regardless of any flag.
    NEVER returns or logs the signing key (F-15 on every error path)."""
    import time as _time
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "contract deploy is admin-only"}, status=403)

    from bot.web import web3_signer as _signer
    from bot.web import web3_exec_gate as _gate
    network = str(body.get("network") or "sepolia")
    bytecode = str(body.get("bytecode") or "").strip()
    cname = str(body.get("contract_name") or "").strip()[:64]
    # Init bytecode is a 0x hex blob — validate shape before touching the signer.
    _bc = bytecode[2:] if bytecode.startswith("0x") else bytecode
    if not _bc or len(_bc) % 2 != 0 or any(c not in "0123456789abcdefABCDEF" for c in _bc):
        return web.json_response({"error": "valid 0x contract bytecode required"}, status=400)
    if len(_bc) > 96000:                          # ~48KB — well past the 24KB EIP-170 limit
        return web.json_response({"error": "bytecode too large"}, status=400)
    bytecode = "0x" + _bc

    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        _store = get_user_authority_store()
        enforcing = bool(_store.is_enforcing(tg_id))
    except Exception:
        _store, enforcing = None, False

    # 1) Signing preconditions — testnet-only, own flag, key + library present.
    decision = _signer.evaluate_sign(is_admin=True, network=network,
                                     envelope_enforcing=enforcing)
    if not decision.allowed:
        return web.json_response({"error": "web3_sign_denied", "reason": decision.reason,
                                  "checklist": decision.checklist}, status=403)

    # 2) The Authority Envelope authorizes THIS deploy (a distinct 'deploy' kind).
    try:
        from bot.guardian.authority import authorize
        env = _store.get(tg_id) if _store else None
        result = authorize(env, {"kind": "deploy", "asset": "ETH", "notional_usd": None,
                                 "dest": None, "network": network},
                           now_ts=_time.time(), spent_today_usd=0.0)
        if result.get("decision") != "allow":
            return web.json_response({"error": "authority_denied",
                                      "reasons": list(result.get("reasons") or ["not authorized"])},
                                     status=403)
        env_id = result.get("envelope_id")
    except Exception:
        return web.json_response({"error": "authority_check_failed"}, status=403)

    # 3) Prepare (nonce + estimated deploy gas + fees), sign the CREATE tx, broadcast.
    signer_addr = _signer.signer_address() or ""
    prep = await _signer.prepare_deploy(network=network, address=signer_addr, bytecode=bytecode)
    if not prep.get("ok"):
        return web.json_response({"error": "prepare_failed", "reason": prep.get("error")},
                                 status=400)
    nonce = int(prep["nonce"])
    signed = _signer.build_and_sign(
        network=network, to=None, value_wei=0, nonce=nonce, data=bytecode,
        gas=int(prep.get("gas") or _signer._DEPLOY_GAS_FALLBACK),
        max_fee_wei=int(prep.get("max_fee_wei") or 2_000_000_000),
        max_priority_wei=int(prep.get("max_priority_wei") or 1_000_000_000))
    if not signed.get("ok"):
        return web.json_response({"error": "sign_failed", "reason": signed.get("error")},
                                 status=400)
    net = decision.network or {}
    bcast = await _signer.broadcast(signed["raw"], _signer.rpc_url_for(network),
                                    net.get("chain_id"))
    # The CREATE address is deterministic in (deployer, nonce) — show it now.
    contract_addr = _signer.create_contract_address(signed.get("from") or signer_addr, nonce)
    tx_hash = bcast.get("tx_hash") or signed.get("tx_hash")

    # 4) Record to the Guardian review queue (fail-safe — never blocks the deploy).
    try:
        from bot.guardian.review_queue import get_review_queue
        get_review_queue().record({"user_id": tg_id, "kind": "contract_deploy", "network": network,
                                   "action": {"side": "deploy", "contract": cname or None,
                                              "address": contract_addr or None, "tx_hash": tx_hash,
                                              "broadcast": bool(bcast.get("ok"))},
                                   "envelope_id": env_id, "ts": _time.time()})
    except Exception:
        pass

    audit(system_log, f"Contract DEPLOY (admin) testnet {network}",
          action="contract_deploy", result="OK" if bcast.get("ok") else "SIGNED",
          data={"network": network, "broadcast": bool(bcast.get("ok"))})
    return web.json_response({
        "deployed": bool(bcast.get("ok")),
        "signed": True,
        "network": network,
        "chain_id": net.get("chain_id"),
        "testnet": True,
        "from": signed.get("from"),
        "contract_name": cname,
        "contract_address": contract_addr,
        "tx_hash": tx_hash,
        "explorer_tx_url": _gate.explorer_tx_url(network, tx_hash or ""),
        "explorer_address_url": _gate.explorer_address_url(network, contract_addr or ""),
        "envelope": {"id": env_id},
        "error": None if bcast.get("ok") else bcast.get("error"),
        "intent": "contract_deploy",
    })


async def handle_cross_plan(request: web.Request) -> web.Response:
    """CROSS-2 — guided cross-chain yield execution PREVIEW (admin-only, read-only).

    Compiles a single scanned move into an execution plan and runs the triple-
    gate (scanner-worth + yield-policy + Authority-Envelope) plus the locked
    hard-gates (stables-only, non-custodial, recallable). This ONLY decides and
    previews — it never signs or broadcasts. When it returns verdict=execute, the
    operator signs the first-leg transfer through the existing gated testnet
    signer (POST /web3/sign). Fail-closed; the signing key is never touched."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "yield execution is admin-only"}, status=403)

    move = body.get("move") if isinstance(body.get("move"), dict) else None
    to_chain = str(body.get("to_chain") or "").strip()
    dest = str(body.get("dest") or "").strip()
    if not move:
        return web.json_response({"error": "move object required"}, status=400)

    import time as _time
    from bot.guardian.yield_plan import evaluate_yield_move, DEFAULT_YIELD_POLICY
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        _store = get_user_authority_store()
        envelope = _store.get(tg_id) if _store else None
    except Exception:
        envelope = None

    decision = evaluate_yield_move(
        move=move, to_chain=to_chain, dest=dest, envelope=envelope,
        now_ts=_time.time(), spent_today_usd=0.0)
    return web.json_response({
        "verdict": decision["verdict"],
        "gates": decision["gates"],
        "reasons": decision["reasons"],
        "first_leg": decision["first_leg"],
        "stables_only_ok": decision["stables_only_ok"],
        "horizon_days": decision["horizon_days"],
        "policy": [dict(r) for r in DEFAULT_YIELD_POLICY],
        "read_only": True,
        "note": ("Preview only — nothing is signed here. When the verdict is "
                 "'execute', sign the first-leg transfer through the admin "
                 "testnet signer; bridge + deposit legs are a later slice."),
        "intent": "cross_yield_plan",
    })


def _setup_from_new_idea(engine, ideas_before: set) -> dict | None:
    """A READ-ONLY setup hint for the web chat's one-tap "Trade this".

    If a skill (e.g. analyze_asset) just registered a fresh tradeable idea in
    engine._pending_ideas, return {symbol, direction, entry, sl, tp, rr,
    confidence} so the client can offer a "Trade this" button. That button
    re-proposes through the SAME manual /trade/propose -> confirm rails (which
    register the proposer and re-run every gate) — this hint never registers a
    proposer, mutates the money path, or makes any trade confirmable on its own.
    Returns None when no new idea with valid levels appeared.
    """
    pending = getattr(engine, "_pending_ideas", {}) or {}
    new_ids = [k for k in pending if k not in ideas_before]
    if not new_ids:
        return None
    idea = pending.get(new_ids[-1])
    try:
        entry = float(idea.entry_price)
        sl = float(idea.stop_loss)
        tp = float(idea.take_profit)
    except (TypeError, ValueError, AttributeError):
        return None
    if not (entry > 0 and sl > 0 and tp > 0):
        return None
    direction = (idea.direction.value if hasattr(idea.direction, "value")
                 else str(idea.direction)).upper()
    if direction not in ("LONG", "SHORT"):
        return None
    rr = getattr(idea, "risk_reward_ratio", None)
    conf = getattr(idea, "confidence", None)
    return {
        "symbol": str(getattr(idea, "asset", "")).split("/")[0],
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": round(float(rr), 2) if isinstance(rr, (int, float)) else None,
        "confidence": round(float(conf), 2) if isinstance(conf, (int, float)) else None,
    }


async def handle_chat_history(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    try:
        limit = min(int(request.query.get("limit", 30)), 100)
    except ValueError:
        limit = 30

    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err

    msgs = tg_handler.conversations.get_recent(tg_id, limit=limit)
    return web.json_response({"messages": [
        {"role": m.role, "content": m.content, "timestamp": m.timestamp}
        for m in msgs
    ]})


async def handle_research_web(request: web.Request) -> web.Response:
    """POST /research/web — AI-4: live, CITED web research for a coin, as an
    opt-in enrichment on the (otherwise local-sources-only) research dossier.

    ADMIN-ONLY: real-time web_search bills the operator's Anthropic key, so —
    exactly like the chat web_search path — only the operator can reach it; a
    non-admin gets a friendly note and zero spend. §4: this rides Anthropic's
    sanctioned server-side web_search tool (NOT scraping); the model returns
    cited public sources and is instructed never to reproduce paywalled or
    credential-gated content. Advisory context, never a verdict. Fail-soft."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    base = re.sub(r"[^A-Za-z0-9]", "", str(body.get("base") or "")).upper()[:10]
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if not base:
        return web.json_response({"error": "base required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response(
            {"error": "admin_only",
             "detail": "Live web research runs on the operator's AI key and is "
                       "available to the operator only."}, status=403)
    prompt = (
        f"Research the crypto asset {base} using live web search. In 4–6 concise "
        f"bullet points, cover only what's genuinely recent and material: notable "
        f"news or catalysts, protocol/tokenomics or listing developments, and any "
        f"security incidents or risk flags in roughly the last 30 days. Cite a "
        f"reputable primary source for each claim and note how recent it is. If "
        f"you can't verify something, say so rather than guessing. This is "
        f"advisory context for a trader — not financial advice and not a verdict. "
        f"Never reproduce paywalled or credential-gated content.")
    try:
        answer, meta = await tg_handler._llm_chat(
            prompt, user_id=tg_id, is_admin=True, return_meta=True)
    except Exception as exc:
        system_log.debug("research web (%s) failed: %s", base, exc)
        return web.json_response({"error": "research_unavailable"}, status=502)
    audit(system_log, f"Web research dossier: {base}",
          action="research_web", result="OK", data={"user": tg_id, "base": base})
    return web.json_response({
        "read_only": True,
        "base": base,
        "web_html": answer,
        "model": (meta or {}).get("model", ""),
        "provider": (meta or {}).get("provider", ""),
        "disclaimer": ("Live web research via the operator's AI with cited public "
                       "sources — advisory context, not financial advice, not a verdict."),
    })


# ── Manual trade propose / confirm / cancel ─────────────────────────────────

def _remember_proposer(app, trade_id: str, tg_id: str) -> None:
    proposers: dict = app["proposers"]
    if len(proposers) >= _MAX_PROPOSERS:
        # Drop oldest entries (insertion-ordered dict) — stale pending ideas
        # expire engine-side anyway.
        for k in list(proposers)[: _MAX_PROPOSERS // 5]:
            proposers.pop(k, None)
    proposers[trade_id] = tg_id


def _web_envelope_enforcing(app, tg_id: str) -> bool:
    """Is a bound Authority Envelope in ENFORCE mode for this web user?

    Reads the per-user Authority Envelope store. Fail-closed: no bound envelope,
    a revoked one, non-enforce mode, or any error → False, so the web live gate
    denies. There is deliberately no way to reach a live web order without an
    enforce-mode envelope.
    """
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        return bool(get_user_authority_store().is_enforcing(tg_id))
    except Exception:
        return False


def _web_live_decision(app, tg_handler, tg_id: str):
    """Evaluate the fail-closed web live gate for a web-only identity."""
    from bot.web import web_live_gate
    has_keys = False
    try:
        from bot.core.exchange_credentials import get_credential_store
        has_keys = bool(get_credential_store().has(tg_id))
    except Exception:
        has_keys = False
    opt_in_fn = getattr(tg_handler.users, "web_live_enabled", None)
    try:
        user_opted_in = bool(opt_in_fn(tg_id)) if callable(opt_in_fn) else False
    except Exception:
        user_opted_in = False
    return web_live_gate.evaluate(
        feature_enabled=web_live_gate.feature_enabled(),
        bot_is_live=CONFIG.is_live(),
        user_opted_in=user_opted_in,
        has_own_keys=has_keys,
        envelope_enforcing=_web_envelope_enforcing(app, tg_id),
    )


_WEB_LIVE_LEDGER = None


def _web_live_ledger():
    """Process-wide 24h notional spend ledger for web-live authority checks."""
    global _WEB_LIVE_LEDGER
    if _WEB_LIVE_LEDGER is None:
        from bot.guardian.authority_ledger import AuthoritySpendLedger
        _WEB_LIVE_LEDGER = AuthoritySpendLedger(
            state_file=os.environ.get("WEB_LIVE_LEDGER_PATH",
                                      "data/web_live_ledger.json"))
    return _WEB_LIVE_LEDGER


def _authorize_web_live_trade(app, engine, tg_id: str, trade_id: str) -> tuple[bool, list]:
    """Authorize a specific web-live trade against the user's ENFORCE-mode
    Authority Envelope. FAIL-CLOSED: any missing piece → deny.

    Reconstructs the trade action (venue, market_type, symbol, notional) from the
    pending idea + the user's active venue and configured leverage, runs
    ``authority.authorize`` against the bound envelope with the 24h spend already
    recorded, and — only on allow — records this trade's notional. Returns
    ``(allowed, reasons)``.
    """
    import time as _time
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        from bot.guardian.authority import authorize
        env = get_user_authority_store().get(tg_id)
        if not env:
            return False, ["no Authority Envelope is bound"]
        idea = getattr(engine, "_pending_ideas", {}).get(trade_id)
        if idea is None:
            return False, ["the proposed trade is no longer pending"]
        asset = str(getattr(idea, "asset", "")).split("/")[0]
        # Active venue for this user (their own connected keys).
        try:
            from bot.core.exchange_credentials import get_credential_store
            venue = get_credential_store().get_venue(tg_id)
        except Exception:
            venue = ""
        # Notional = manual margin × configured leverage. Auto-sized (no margin)
        # → notional unknown → authorize() denies against any per-trade cap.
        margin = getattr(engine, "_manual_margin_override", {}).get(trade_id)
        notional = None
        if margin is not None:
            try:
                exch = getattr(CONFIG, "exchange", None)
                lev = float(getattr(exch, "default_leverage", 5) or 5)
                notional = float(margin) * max(1.0, lev)
            except (TypeError, ValueError):
                notional = None
        action = {"kind": "trade", "venue": venue, "market_type": "swap",
                  "asset": asset, "notional_usd": notional}
        now = _time.time()
        ledger = _web_live_ledger()
        spent = ledger.spent(tg_id, now)
        result = authorize(env, action, now_ts=now, spent_today_usd=spent)
        if result.get("decision") != "allow":
            return False, list(result.get("reasons") or ["not authorized"])
        if notional:
            ledger.record(tg_id, notional, now, ref=trade_id)
        return True, []
    except Exception as exc:
        system_log.warning("Web-live authorization error for %s: %s", tg_id, exc)
        return False, ["authorization check failed"]


def _trade_mode(app, tg_handler, tg_id: str) -> tuple[str, bool, str]:
    """(mode, live_allowed, reason) — the live-execution decision.

    Telegram identities follow the operator allowlist + UserStore flag exactly.
    Web-only identities are paper by default and can reach LIVE only through the
    separate, fail-closed web live gate (own keys + operator feature switch +
    per-user opt-in + enforce-mode Authority Envelope) — a tampered users.json
    entry alone (web:N with role=admin) never yields LIVE.
    """
    if _is_web_id(tg_id):
        dec = _web_live_decision(app, tg_handler, tg_id)
        if dec.allowed and CONFIG.is_live():
            return "LIVE", True, dec.reason
        return "PAPER", False, dec.reason
    live_allowed = bool(_is_admin_id(tg_handler, tg_id)
                        or tg_handler._can_trade_live(tg_id))
    mode = "LIVE" if (CONFIG.is_live() and live_allowed) else "PAPER"
    return mode, live_allowed, ""


def _idea_payload(app, tg_handler, tg_id: str, idea, margin_usd) -> dict:
    entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
    mode, live_allowed, live_reason = _trade_mode(app, tg_handler, tg_id)
    return {
        "trade_id": idea.id,
        "symbol": idea.asset.split("/")[0],
        "direction": idea.direction.value if hasattr(idea.direction, "value") else str(idea.direction),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": idea.risk_reward_ratio,
        "sl_pct": round(abs(entry - sl) / entry * 100, 2),
        "tp_pct": round(abs(tp - entry) / entry * 100, 2),
        "margin_usd": margin_usd,
        "order_type": getattr(idea, "order_type", "limit") or "limit",
        "mode": mode,
        "live_allowed": live_allowed,
        "live_reason": live_reason,
    }


def _propose_from_text(app, tg_handler, engine, tg_id: str, text: str,
                       name: str = "", order_type: str = "limit") -> web.Response:
    from bot.skills.manual_trade import (parse_manual_trade, build_manual_idea,
                                         register_manual_idea)
    err = _guard_user(tg_handler, tg_id, command="trade", name=name)
    if err is not None:
        return err
    parsed = parse_manual_trade(text)
    if isinstance(parsed, str):
        return web.json_response({"error": "invalid_trade", "detail": parsed},
                                 status=400)
    direction, symbol, entry, sl, tp, margin_usd = parsed
    try:
        idea = build_manual_idea(direction, symbol, entry, sl, tp,
                                 order_type=order_type)
    except ValueError as e:
        return web.json_response({"error": "invalid_trade",
                                  "detail": _html.escape(str(e))}, status=400)
    register_manual_idea(engine, idea, margin_usd)
    _remember_proposer(app, idea.id, tg_id)
    audit(system_log,
          f"Web manual trade created: {idea.id} {direction} {symbol}/USDT "
          f"entry={entry} sl={sl} tp={tp}",
          action="web_manual_trade_created", result="PENDING",
          data={"user": tg_id})
    return web.json_response(
        {"pending_trade": _idea_payload(app, tg_handler, tg_id, idea, margin_usd)})


async def handle_trade_propose(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    name = str(body.get("name") or "").strip()[:64]
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)

    # Accept raw text ("buy SOL 71 sl 70 tp 76") or structured fields; both are
    # normalized through the ONE shared parser so validation cannot drift.
    # Order type: 'market' (open now) or 'limit' (rest at entry). Default limit
    # keeps the platform's historical maker-only behaviour; the parser grammar
    # has no place for it, so it rides alongside the reassembled text.
    from bot.skills.manual_trade import normalize_order_type
    order_type = normalize_order_type(body.get("order_type"))

    text = str(body.get("text") or "").strip()
    if not text:
        try:
            direction = str(body.get("direction") or "").strip().upper()
            symbol = str(body.get("symbol") or "").strip().upper()
            entry = float(body.get("entry"))
            sl = float(body.get("sl"))
            tp = float(body.get("tp"))
            margin = body.get("margin")
            margin_txt = f" margin {float(margin)}" if margin not in (None, "", 0) else ""
        except (TypeError, ValueError):
            return web.json_response(
                {"error": "invalid_trade",
                 "detail": "direction, symbol, entry, sl, tp must be provided"},
                status=400)
        if direction not in ("LONG", "SHORT", "BUY", "SELL"):
            return web.json_response(
                {"error": "invalid_trade", "detail": "direction must be LONG or SHORT"},
                status=400)
        text = f"{direction} {symbol} {entry} sl {sl} tp {tp}{margin_txt}"
    return _propose_from_text(request.app, tg_handler, engine, tg_id, text,
                              name=name, order_type=order_type)


async def handle_trade_confirm(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    trade_id = str(body.get("trade_id") or "").strip()
    if not tg_id or not trade_id:
        return web.json_response({"error": "telegram_id and trade_id required"},
                                 status=400)
    err = _guard_user(tg_handler, tg_id, command="trade")
    if err is not None:
        return err
    # Proposer isolation: a web user may only confirm ideas THEY proposed via
    # this gateway — never the engine's auto-generated pending ideas and never
    # another user's proposal.
    if request.app["proposers"].get(trade_id) != tg_id:
        return web.json_response({"error": "not_proposer"}, status=403)
    # Web-only identities reach LIVE only through the fail-closed web live gate
    # (operator feature switch + own keys + per-user opt-in + enforce-mode
    # Authority Envelope). By default the gate denies, so this stays paper-only
    # exactly as before — but a tampered users.json entry alone (web:N with
    # role=admin) can never satisfy the gate, so it never opens a live path.
    if CONFIG.is_live() and _is_web_id(tg_id):
        dec = _web_live_decision(request.app, tg_handler, tg_id)
        if not dec.allowed:
            return web.json_response(
                {"error": "live_not_enabled", "detail": dec.reason,
                 "checklist": dec.checklist}, status=403)
        # Gate passed → this trade will route LIVE on the user's own keys. The
        # enforce-mode Authority Envelope must now authorize THIS specific order
        # (venue, symbol, notional, 24h spend). Fail-closed: any deny blocks it.
        ok, reasons = _authorize_web_live_trade(request.app, engine, tg_id, trade_id)
        if not ok:
            audit(system_log, f"Web-live trade DENIED by authority: {trade_id}",
                  action="web_authority_deny", result="DENY", data={"user": tg_id})
            return web.json_response(
                {"error": "authority_denied", "detail": "; ".join(reasons),
                 "reasons": reasons}, status=403)
    # Live gate — same H-18 check as the Telegram confirm path (non-web ids).
    elif CONFIG.is_live() and not _is_admin_id(tg_handler, tg_id):
        if not tg_handler._can_trade_live(tg_id):
            return web.json_response({"error": "live_not_enabled"}, status=403)
    result = await engine.confirm_trade(trade_id, user_id=tg_id)
    request.app["proposers"].pop(trade_id, None)
    audit(system_log, f"Web trade confirm: {trade_id}",
          action="web_trade_confirm", result="OK", data={"user": tg_id})
    return web.json_response({"result_html": result})


async def handle_trade_live_mode(request: web.Request) -> web.Response:
    """Authoritative live-capability for an identity — the SAME decision the
    propose/confirm path makes (_trade_mode), covering both linked Telegram
    users (operator allowlist + /live) and web-only ids (fail-closed web-live
    gate). The web layer gates its 2FA step-up on THIS, not on a stale
    user_controls mirror that is empty for exactly those two live paths.
    Read-only; guarded like every other per-user endpoint."""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    mode, live_allowed, _reason = _trade_mode(request.app, tg_handler, tg_id)
    return web.json_response({"mode": mode, "live_allowed": bool(live_allowed)})


async def handle_trade_copilot(request: web.Request) -> web.Response:
    """POST /gateway/trade/copilot — a deterministic second opinion on a
    proposed trade BEFORE the user confirms. Read-only advice; places nothing.

    Body: ``{telegram_id, direction, symbol, entry, sl, tp, margin?}``. The
    gateway enriches with the caller's paper equity (for the size check); engine
    bias / existing exposure are passed through when the client supplies them.
    """
    tg_handler = request.app["tg_handler"]
    engine = request.app["engine"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id, command="trade")
    if err is not None:
        return err
    trade = {k: body.get(k) for k in ("direction", "symbol", "entry", "sl", "tp", "margin")}
    equity = None
    try:
        snap = engine.user_portfolios.get(tg_id).snapshot()
        equity = float(snap.equity_usd)
    except Exception:
        equity = None
    bias = body.get("engine_bias") if body.get("engine_bias") in ("long", "short") else None
    expo = body.get("existing_exposure") if body.get("existing_exposure") in ("long", "short") else None
    try:
        from bot.core.trade_copilot import review, human_readable
        rev = review(trade, equity_usd=equity, engine_bias=bias, existing_exposure=expo)
        rev["human_readable"] = human_readable(rev)
        return web.json_response(rev)
    except Exception as exc:
        system_log.debug("Trade co-pilot failed: %s", exc)
        return web.json_response({"error": "copilot_unavailable"}, status=500)


async def handle_trade_cancel(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    trade_id = str(body.get("trade_id") or "").strip()
    if not tg_id or not trade_id:
        return web.json_response({"error": "telegram_id and trade_id required"},
                                 status=400)
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if request.app["proposers"].get(trade_id) != tg_id:
        return web.json_response({"error": "not_proposer"}, status=403)
    idea = engine._pending_ideas.get(trade_id)
    if idea is not None and getattr(idea, "source", "") == "manual":
        engine._pending_ideas.pop(trade_id, None)
        if hasattr(engine, "_manual_margin_override"):
            engine._manual_margin_override.pop(trade_id, None)
    request.app["proposers"].pop(trade_id, None)
    audit(system_log, f"Web trade cancel: {trade_id}",
          action="web_trade_cancel", result="CANCELLED", data={"user": tg_id})
    return web.json_response({"cancelled": True})


# ── Per-user portfolio snapshot ──────────────────────────────────────────────

def _trade_row(t) -> dict:
    """Serialize a TradeExecution for the website (paper portfolio)."""
    return {
        "trade_id": t.trade_id,
        "symbol": t.asset,
        "direction": t.direction.value if hasattr(t.direction, "value") else str(t.direction),
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "quantity": t.quantity,
        "size_usd": round(t.entry_price * t.quantity, 2),
        "stop_loss": t.stop_loss,
        "take_profit": t.take_profit,
        "leverage": t.leverage,
        "pnl": t.pnl,
        "commission": t.commission,
        "strategy_type": t.strategy_type,
        "opened_at": t.opened_at.isoformat() if t.opened_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
    }


async def handle_portfolio(request: web.Request) -> web.Response:
    """GET /gateway/portfolio?telegram_id=... — the caller's own PAPER
    portfolio truth (equity, open positions, recent closed trades) from
    engine.user_portfolios. This is what the website's per-user dashboard
    renders; the operator sync channel stays untouched."""
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    try:
        tracker = engine.user_portfolios.get(tg_id)
        snap = tracker.snapshot()
        open_rows = [_trade_row(t) for t in tracker.open_positions]
        closed_rows = [_trade_row(t) for t in tracker.trade_history[-100:]]
    except Exception as exc:
        audit(system_log, f"Web portfolio read failed for {tg_id}: {exc}",
              action="web_portfolio", result="ERROR")
        return web.json_response({"error": "portfolio_unavailable"}, status=503)
    return web.json_response({
        "mode": "PAPER" if not CONFIG.is_live() or _is_web_id(tg_id) else "MIXED",
        "equity": snap.equity_usd,
        "balance": snap.balance_usd,
        "total_pnl": snap.total_pnl,
        "daily_pnl": snap.daily_pnl,
        "win_rate": snap.win_rate,
        "total_trades": snap.total_trades,
        "open_positions": open_rows,
        "closed_trades": closed_rows,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Open positions + stop-loss PROTECTION TRUTH (read-only) ──────────────────

def _protection_dists(entry: float, sl: float, tp: float) -> tuple[float, float]:
    sl_d = abs(entry - sl) / entry * 100 if entry > 0 and sl > 0 else 0.0
    tp_d = abs(tp - entry) / entry * 100 if entry > 0 and tp > 0 else 0.0
    return round(sl_d, 2), round(tp_d, 2)


def _live_position_row(pos) -> dict:
    """Serialize a LIVE LivePosition with stop-loss protection truth.

    The §4 truth field is ``pos.sl_order_id``: non-null ⇒ a stop order is tracked
    on the exchange (protected); None with a stop price set ⇒ the position is
    UNPROTECTED (real risk — the exact thing the operator fought to see). Mirrors
    Telegram's ``sl_order: 'exchange' if pos.sl_order_id else 'manual'``.
    """
    entry = float(getattr(pos, "entry_price", 0) or 0)
    sl = float(getattr(pos, "stop_loss", 0) or 0)
    tp = float(getattr(pos, "take_profit", 0) or 0)
    qty = float(getattr(pos, "quantity", 0) or 0)
    cost = float(getattr(pos, "cost_usd", 0) or 0) or (entry * qty)
    lev = float(getattr(pos, "leverage", 0) or 0) or 1.0
    sl_protected = bool(getattr(pos, "sl_order_id", None))
    tp_protected = bool(getattr(pos, "tp_order_id", None))
    unprotected = (not sl_protected and sl > 0) or bool(getattr(pos, "unprotected", False))
    sl_d, tp_d = _protection_dists(entry, sl, tp)
    opened = getattr(pos, "opened_at", None)
    return {
        "symbol": getattr(pos, "symbol", ""),
        "pair": str(getattr(pos, "symbol", "")).split("/")[0],
        "direction": getattr(pos, "direction", ""),
        "entry_price": round(entry, 6),
        "stop_loss": round(sl, 6),
        "take_profit": round(tp, 6),
        "sl_dist_pct": sl_d,
        "tp_dist_pct": tp_d,
        "size_usd": round(cost, 2),
        "leverage": round(lev, 2),
        "quantity": qty,
        "sl_order": "exchange" if sl_protected else "manual",
        "tp_order": "exchange" if tp_protected else "manual",
        "sl_protected": sl_protected,
        "tp_protected": tp_protected,
        "unprotected": unprotected,
        "strategy_type": getattr(pos, "strategy_type", "") or "",
        "opened_at": opened.isoformat() if opened else None,
    }


def _paper_position_row(t) -> dict:
    """Serialize a PAPER TradeExecution. There is no exchange, so the stop is
    bot-managed in-sim — truthfully 'bot-managed', NOT an 'unprotected' alarm
    (that red state only means a live position missing its exchange stop)."""
    row = _trade_row(t)
    entry = float(getattr(t, "entry_price", 0) or 0)
    sl = float(getattr(t, "stop_loss", 0) or 0)
    tp = float(getattr(t, "take_profit", 0) or 0)
    sl_d, tp_d = _protection_dists(entry, sl, tp)
    row["pair"] = str(getattr(t, "asset", "")).split("/")[0]
    row["sl_dist_pct"] = sl_d
    row["tp_dist_pct"] = tp_d
    row["sl_order"] = "manual"
    row["tp_order"] = "manual"
    row["sl_protected"] = False
    row["tp_protected"] = False
    row["unprotected"] = False
    return row


async def handle_positions(request: web.Request) -> web.Response:
    """GET /gateway/positions?telegram_id=... — the caller's OPEN positions with
    stop-loss PROTECTION TRUTH: is each stop actually live on the exchange, or
    bot-managed? Read-only mirror of Telegram /open_positions — NOTHING here
    places, moves, sizes, or closes an order.

    Live users read from their own executor (protection truth = pos.sl_order_id);
    paper/web-only users read the in-sim tracker (stops are bot-managed)."""
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    rows: list[dict] = []
    live = False
    try:
        if CONFIG.is_live() and not _is_web_id(tg_id):
            executor = engine._executor_for(tg_id)
            live_positions = list(getattr(executor, "open_positions", []) or []) if executor else []
            rows = [_live_position_row(p) for p in live_positions]
            live = True
        else:
            tracker = engine.user_portfolios.get(tg_id)
            rows = [_paper_position_row(t) for t in tracker.open_positions]
    except Exception as exc:
        audit(system_log, f"Web positions read failed for {tg_id}: {exc}",
              action="web_positions", result="ERROR")
        return web.json_response({"error": "positions_unavailable"}, status=503)
    return web.json_response({
        "live": live,
        "read_only": True,
        "positions": rows,
        "count": len(rows),
        "protected_count": sum(1 for r in rows if r.get("sl_protected")),
        "unprotected_count": sum(1 for r in rows if r.get("unprotected")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


# ── Cross-venue net worth (read-only) ────────────────────────────────────────

async def handle_networth(request: web.Request) -> web.Response:
    """GET /gateway/networth?telegram_id=... — the caller's own cross-venue
    snapshot: paper equity plus (when they connected an exchange) ONE
    read-only balance fetch on their stored venue.

    Credentials are decrypted in-process for the fetch and never appear in
    the response; nothing here can place, modify, or cancel an order — it is
    the same read-only call the connect-time validators make.
    """
    import asyncio

    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err

    paper = None
    try:
        snap = engine.user_portfolios.get(tg_id).snapshot()
        paper = {"equity_usd": round(float(snap.equity_usd), 2),
                 "total_pnl": round(float(snap.total_pnl), 2),
                 "simulated": True}
    except Exception:
        paper = None                                   # section says so

    cex: dict = {"connected": False}
    try:
        from bot.core.exchange_credentials import (
            get_credential_store, balance_snapshot)
        store = get_credential_store()
        if store.has(tg_id):
            venue = store.get_venue(tg_id)
            fields = store.get(tg_id)
            if not fields:
                cex = {"connected": True, "venue": venue, "ok": False,
                       "equity_usd": None, "detail": "credentials unreadable"}
            else:
                try:
                    snap_cex = await asyncio.wait_for(
                        balance_snapshot(venue, fields), timeout=25)
                except asyncio.TimeoutError:
                    snap_cex = {"ok": False, "venue": venue,
                                "equity_usd": None, "detail": "venue timeout"}
                cex = {"connected": True, **snap_cex}
    except Exception as exc:
        audit(system_log, f"Net-worth CEX read failed for {tg_id}: {exc}",
              action="web_networth", result="ERROR")
        cex = {"connected": False, "error": "cex_unavailable"}

    return web.json_response({
        "read_only": True,
        "paper": paper,
        "cex": cex,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


async def handle_holdings(request: web.Request) -> web.Response:
    """GET /gateway/holdings?telegram_id=... — per-venue read-only balances across
    ALL of the caller's connected venues (not just the active one), for the
    "funds by venue & wallet" view.

    One read-only balance fetch per connected venue, bounded concurrency. Same
    trust surface as handle_networth: credentials decrypted in-process only, never
    in the response; nothing here can place, modify, or cancel an order. A venue
    that errors returns its error row — never a fabricated zero.
    """
    import asyncio

    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err

    rows: list[dict] = []
    try:
        from bot.core.exchange_credentials import (
            get_credential_store, balance_snapshot)
        store = get_credential_store()
        venues = store.list_venues(tg_id)          # every connected venue
        active = store.get_venue(tg_id)
        sem = asyncio.Semaphore(4)                  # bound concurrent upstream sockets

        async def _one(venue: str) -> dict:
            fields = store.get_for_venue(tg_id, venue)
            if not fields:
                return {"venue": venue, "ok": False, "equity_usd": None,
                        "detail": "credentials unreadable"}
            async with sem:
                try:
                    snap = await asyncio.wait_for(
                        balance_snapshot(venue, fields), timeout=25)
                except asyncio.TimeoutError:
                    snap = {"ok": False, "venue": venue, "equity_usd": None,
                            "detail": "venue timeout"}
            return {"venue": venue, "active": venue == active, **snap}

        if venues:
            rows = list(await asyncio.gather(*[_one(v) for v in venues]))
    except Exception as exc:
        audit(system_log, f"Holdings read failed for {tg_id}: {exc}",
              action="web_holdings", result="ERROR")
        return web.json_response({"read_only": True, "venues": [],
                                  "error": "holdings_unavailable"})

    total = 0.0
    for r in rows:
        v = r.get("equity_usd")
        if r.get("ok") and isinstance(v, (int, float)):
            total += float(v)
    return web.json_response({
        "read_only": True,
        "venues": rows,
        "venue_total_usd": round(total, 2),
        "venue_count": len(rows),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


async def handle_sentry(request: web.Request) -> web.Response:
    """GET /gateway/sentry?telegram_id=... — proactive risk watch over the
    caller's standing book (envelope drift, over-cap, concentration, crowding,
    daily-spend). DETECTION-ONLY; nothing is closed or resized."""
    import time as _time
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    positions: list[dict] = []
    equity = None
    try:
        pf = engine.user_portfolios.get(tg_id)
        for t in pf.open_positions:
            price = float(getattr(t, "entry_price", 0) or 0)
            qty = float(getattr(t, "quantity", 0) or 0)
            side = getattr(getattr(t, "direction", None), "value", None) or str(getattr(t, "direction", ""))
            positions.append({"symbol": getattr(t, "asset", ""), "side": side,
                              "notional_usd": price * qty})
        equity = float(pf.snapshot().equity_usd)
    except Exception:
        positions, equity = [], None
    envelope = None
    spent = 0.0
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        envelope = get_user_authority_store().get(tg_id)
    except Exception:
        envelope = None
    try:
        spent = _web_live_ledger().spent(tg_id, _time.time())
    except Exception:
        spent = 0.0
    try:
        from bot.guardian.risk_sentry import assess
        report = assess(positions, envelope=envelope, equity_usd=equity,
                        spent_today_usd=spent)
    except Exception as exc:
        system_log.debug("Risk sentry failed for %s: %s", tg_id, exc)
        return web.json_response({"error": "sentry_unavailable"}, status=500)
    report["read_only"] = True
    report["envelope_bound"] = envelope is not None
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    return web.json_response(report)


async def handle_news(request: web.Request) -> web.Response:
    """GET /gateway/news?telegram_id=... — NEWS-1c web surface. Public-RSS
    headline radar with high-impact flags on the caller's held positions.
    READ-ONLY / advisory: nothing here moves, sizes, or blocks a trade."""
    import time as _time

    from bot.core.news import NewsRadar
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err

    enabled = NewsRadar.enabled()
    radar = getattr(engine, "_news_radar", None)
    if radar is None:
        radar = NewsRadar()
        try:
            engine._news_radar = radar
        except Exception:
            pass

    held: list = []
    try:
        pf = engine.user_portfolios.get(tg_id)
        for t in pf.open_positions:
            s = getattr(t, "asset", "") or getattr(t, "symbol", "")
            if s:
                held.append(s)
    except Exception:
        held = []

    if enabled:
        try:
            await radar.refresh(
                symbols=held or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"])
        except Exception as exc:
            system_log.debug("news web refresh failed: %s", exc)

    now = _time.time()

    def _item(it) -> dict:
        return {"title": it.title, "url": it.url, "source": it.source,
                "impact": it.impact.value, "reasons": list(it.impact_reasons),
                "symbols": list(it.symbols), "age_sec": int(it.age_sec(now))}

    # NEWS-2: if the caller has connected their own paid news key, enrich THEIR
    # feed with it — never the operator's cost, never seen by other users. §4:
    # headline + source + link only (fetch_byon_news maps public fields only),
    # fail-soft to [] so a bad key silently falls back to the public radar.
    byon: list[dict] = []
    byon_active = False
    try:
        from bot.core import news_byon
        from bot.db.models import get_user_news_key, settings_user_id
        uid = settings_user_id(tg_id)
        if uid is not None:
            provider, key = get_user_news_key(uid)
            if provider and key:
                byon_active = True
                byon = await news_byon.fetch_byon_news(
                    provider, key,
                    held or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"],
                    now)
    except Exception as exc:
        system_log.debug("byon news enrich failed: %s", exc)

    return web.json_response({
        "enabled": enabled,
        "read_only": True,
        "recent": [_item(i) for i in radar.recent(12)],
        "high_impact": [_item(i) for i in radar.high_impact(8)],
        "standdown": radar.standdown(held, now) if held else [],
        "byon": byon,
        "byon_active": byon_active,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })


def _proofofpnl_payload() -> dict:
    """Read the latest sealed Proof-of-PnL publication and re-verify it. Shared by
    the per-user and public handlers — the publication is public-safe by
    construction (``build_publication`` refuses anything else), so the same
    payload is safe to serve with or without auth."""
    import time as _time
    try:
        from bot.proofofpnl.publish import (get_publication_store, verify_publication,
                                            is_fresh)
        pub = get_publication_store().read()
    except Exception as exc:
        system_log.debug("Proof-of-PnL read failed: %s", exc)
        return {"published": False, "error": "unavailable"}
    if not pub:
        return {
            "published": False,
            "note": "No Proof-of-PnL statement has been published yet. The "
                    "publisher seals one each epoch from raw fills.",
        }
    ok, problems = verify_publication(pub)
    now = int(_time.time())
    return {
        "published": True,
        "publication": pub,
        "verified": ok,
        "problems": problems,
        "fresh": is_fresh(pub, now),
        "age_seconds": max(0, now - int(pub.get("published_at") or now)),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


async def handle_proofofpnl(request: web.Request) -> web.Response:
    """GET /gateway/proofofpnl — the latest CONTINUOUSLY-PUBLISHED Proof-of-PnL
    statement: the public-safe bundle, its freshness, re-derived integrity, and
    the anchor's (honest) UNVERIFIED status. 'Don't trust the dashboard — verify
    the fills.'"""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    return web.json_response(_proofofpnl_payload())


async def handle_proofofpnl_public(request: web.Request) -> web.Response:
    """GET /gateway/public/proofofpnl — the SAME sealed statement, no auth. The
    whole point of the moat is that anyone can verify it without trusting us or
    logging in; the bundle is public-safe by construction, so this is deliberate,
    not a leak. The visitor's browser re-derives the hash on the public page."""
    return web.json_response(_proofofpnl_payload())


def _leaderboard_payload(season: str = "") -> dict:
    """The public verifiable leaderboard: opted-in agents ranked by their
    RE-VERIFIABLE record. Each row is anonymous (handle only), size-agnostic (no
    dollar figure), and carries the publish_hash so anyone can re-derive it.
    Rows that fail re-verification are excluded by the ranker.

    With ``season`` (e.g. '2026-07'): the FROZEN standings for that calendar
    month — statements as sealed during the window, ranked through the same
    re-verify-or-exclude path (bot/proofofpnl/seasons.py). An unknown or
    malformed season yields an empty board, never an error."""
    try:
        floor = int(str(os.environ.get("PROOFOFPNL_LEADERBOARD_MIN_TRIPS", "") or 1))
    except (TypeError, ValueError):
        floor = 1
    floor = max(1, floor)
    season = str(season or "").strip()
    rows: list = []
    seasons: list = []
    try:
        from bot.proofofpnl.seasons import get_season_store
        store = get_season_store()
        seasons = store.season_ids()[:24]
        if season:
            rows = store.ranked(season, min_round_trips=floor, limit=50)
        else:
            from bot.proofofpnl.leaderboard import get_leaderboard_registry
            rows = get_leaderboard_registry().ranked(min_round_trips=floor, limit=50)
    except Exception:
        rows = []
    payload = {"format": "runeclaw.proofofpnl.leaderboard.v0",
               "rows": rows, "count": len(rows), "seasons": seasons}
    if season:
        payload["season"] = season
    return payload


async def handle_leaderboard_public(request: web.Request) -> web.Response:
    """GET /gateway/public/leaderboard[?season=YYYY-MM] — the ranked, anonymous,
    verifiable board (live, or a season's frozen standings), no auth. Same
    discipline as the public Proof-of-PnL feed: every row is public-safe by
    construction and independently re-verifiable, so serving it openly is the
    point, not a leak."""
    season = (request.query.get("season") or "").strip()[:7]
    if season and not re.match(r"^\d{4}-\d{2}$", season):
        season = ""
    return web.json_response(_leaderboard_payload(season))


# ── Public agent directory (ERC-8004 identity card) ──────────────────────────

_AGENT_ADDR_RE = re.compile(r"^0x[0-9a-f]{40}$")


async def handle_agent_card_public(request: web.Request) -> web.Response:
    """GET /gateway/public/agent/{address} — the agent's ERC-8004 identity card,
    no auth. Serves the card ALREADY embedded in the latest public-safe
    publication (the same bundle /proof serves openly), re-verified at read
    time: the returned ``verified`` flag is a fresh hash+signature check, never
    a stored claim. 404 for any address that is not the published agent —
    the directory only ever states what a sealed publication backs."""
    addr = str(request.match_info.get("address") or "").strip().lower()
    if not _AGENT_ADDR_RE.match(addr):
        return web.json_response({"error": "invalid_address"}, status=400)
    try:
        from bot.proofofpnl.erc8004 import human_readable, verify_card
        from bot.proofofpnl.publish import get_publication_store
        pub = get_publication_store().read()
    except Exception:
        pub = None
    card = ((pub or {}).get("bundle") or {}).get("identity_card")
    card_addr = str(((card or {}).get("identity") or {}).get("agent_address") or "")
    if not card or card_addr.lower() != addr:
        return web.json_response({"error": "unknown_agent"}, status=404)
    ok, problems = verify_card(card)
    return web.json_response({
        "card": card,
        "verified": bool(ok),
        "problems": problems,
        "human": human_readable(card),
        "publication": {
            "publish_hash": pub.get("publish_hash"),
            "published_at": pub.get("published_at"),
            "trust_tier": pub.get("trust_tier"),
            "reconciliation": pub.get("reconciliation"),
        },
    })


# ── Share card (privacy-safe PNG for the web share flow) ─────────────────────

_SHARE_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,15}$")


async def handle_share_card(request: web.Request) -> web.Response:
    """GET /gateway/share-card?symbol=&direction=&pnl_pct= — PNG bytes.

    The card is a pure function of three public inputs (symbol, direction,
    PnL percent); no account, user store, or dollar figure is ever in scope on
    this path, which is exactly why it is safe to render on request. Inputs are
    clamped hard because they are caller-supplied. This is the gateway's only
    binary (non-JSON) response; callers must not route it through JSON relays.
    """
    symbol = (request.query.get("symbol") or "").upper().strip()
    direction = (request.query.get("direction") or "").upper().strip()
    if not _SHARE_SYMBOL_RE.match(symbol) or direction not in ("LONG", "SHORT"):
        return web.json_response({"error": "invalid"}, status=400)
    try:
        pnl_pct = float(request.query.get("pnl_pct") or "")
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_pnl"}, status=400)
    if not math.isfinite(pnl_pct):
        return web.json_response({"error": "bad_pnl"}, status=400)
    pnl_pct = max(-100000.0, min(100000.0, pnl_pct))
    try:
        from bot.formatters.signal_card import render_share_card
        png = render_share_card(
            {"symbol": symbol, "direction": direction, "pnl_pct": pnl_pct})
    except Exception:
        png = b""
    if not png:
        return web.json_response({"error": "render_unavailable"}, status=503)
    return web.Response(body=png, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=300"})


# ── Idle-Asset Yield Optimizer (read-only recommendation) ────────────────────
#
# One brain, one language: the optimizer lives in Python (bot.core.idle_yield);
# the web POSTs the caller's idle holdings and gets back the best cross-source
# rate per asset — non-custodial (Lido/Aave, live) preferred honestly over a
# marginally-higher custodial CEX Earn rate. Recommendation-only: this endpoint
# never moves a cent (there is no execution code here at all).

_NONCUSTODIAL_CACHE: dict = {"at": 0.0, "options": []}
_NONCUSTODIAL_TTL_SEC = 900.0        # 15 min — DeFi rates move slowly


def _noncustodial_options_cached() -> list:
    """Live non-custodial options with a 15-min in-process cache. Honest-empty
    on a fetch failure (never a stale-forever or fabricated rate)."""
    now = time.monotonic()
    if now - _NONCUSTODIAL_CACHE["at"] < _NONCUSTODIAL_TTL_SEC and _NONCUSTODIAL_CACHE["options"]:
        return _NONCUSTODIAL_CACHE["options"]
    try:
        from bot.core.idle_yield_feeds import fetch_noncustodial_options
        opts = fetch_noncustodial_options()
    except Exception as exc:
        system_log.debug("Idle-yield non-custodial fetch failed: %s", exc)
        opts = []
    if opts:                              # only cache a good result
        _NONCUSTODIAL_CACHE["at"] = now
        _NONCUSTODIAL_CACHE["options"] = opts
    return opts


def _sanitize_holdings(raw: object) -> list:
    """Accept only well-shaped {asset, usd_value} rows from the client; drop
    everything else. The optimizer is pure, but this keeps a malformed body
    from reaching it."""
    out = []
    if not isinstance(raw, list):
        return out
    for h in raw[:200]:                   # bound the work
        if not isinstance(h, dict):
            continue
        asset = str(h.get("asset") or "").upper().strip()[:20]
        try:
            usd = float(h.get("usd_value"))
        except (TypeError, ValueError):
            continue
        if not asset or not (usd == usd) or usd <= 0 or usd in (float("inf"), float("-inf")):
            continue
        row = {"asset": asset, "usd_value": usd}
        if h.get("location"):
            row["location"] = str(h.get("location"))[:40]
        out.append(row)
    return out


async def handle_idle_yield(request: web.Request) -> web.Response:
    """POST /gateway/idleyield — best cross-source rate per idle asset.

    Body: ``{telegram_id, holdings:[{asset,usd_value,location?}], prefer_noncustodial?}``.
    Builds options from LIVE non-custodial feeds (Lido/Aave via DefiLlama) and
    runs the pure optimizer. Read-only; nothing is moved.
    """
    import asyncio

    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err

    holdings = _sanitize_holdings(body.get("holdings"))
    prefer_nc = body.get("prefer_noncustodial")
    prefer_nc = True if prefer_nc is None else bool(prefer_nc)
    if not holdings:
        return web.json_response({"read_only": True, "recommendations": [],
                                  "total_idle_usd": 0.0, "total_deployable_usd": 0.0,
                                  "total_est_year_usd": 0.0, "unmatched": [],
                                  "note": "No idle assets supplied."})
    try:
        options = await asyncio.to_thread(_noncustodial_options_cached)
        from bot.core.idle_yield import optimize
        report = optimize(holdings, options, prefer_noncustodial=prefer_nc)
    except Exception as exc:
        audit(system_log, f"Idle-yield optimize failed for {tg_id}: {exc}",
              action="web_idleyield", result="ERROR")
        return web.json_response({"read_only": True, "error": "idleyield_unavailable"})
    report["read_only"] = True
    report["sources"] = {"noncustodial": len(options)}
    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    return web.json_response(report)


# ── Authority Envelope authoring (per-user, SELF-SERVE) ──────────────────────
#
# A user describes, in plain words, what their agent may do — "only majors, max
# $500 a trade, $2k a day, only on Bitget" — and this compiles it to a hashed,
# tighten-only Authority Envelope bound to THEIR id. An enforce-mode envelope is
# the custody precondition the web live gate requires. Every step is
# deterministic + fail-open (a hiccup never binds a looser envelope than typed);
# apply RECOMPILES from the text bot-side (never trusts a client blob).

_AUTHORITY_TEXT_MAX = 600


def _compile_user_envelope(text: str, mode: str = "shadow"):
    """NL → compiled, clamped Authority Envelope for a self-serve user."""
    from bot.guardian.authority_nl import compile_nl_envelope
    from bot.guardian.authority import compile_envelope
    try:
        from bot.core.venues import valid_venue_ids
        universe = list(valid_venue_ids())
    except Exception:
        universe = None
    parsed = compile_nl_envelope(text)
    spec = dict(parsed["spec"])
    spec["mode"] = mode if mode in ("off", "shadow", "enforce") else "shadow"
    spec.setdefault("label", "My trading authority")
    env = compile_envelope(spec, venue_universe=universe)
    return env, parsed


async def handle_authority_preview(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    text = str(body.get("text") or "").strip()[:_AUTHORITY_TEXT_MAX]
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    try:
        from bot.guardian.authority import human_readable
        env, parsed = _compile_user_envelope(text, "shadow")
        return web.json_response({
            "ok": True, "human_readable": human_readable(env),
            "matched": parsed["matched"], "pending": parsed["pending"],
            "unmatched": parsed["unmatched"], "envelope_id": env.get("envelope_id"),
            "warnings": env.get("warnings", []),
        })
    except Exception as exc:
        system_log.debug("Authority preview failed: %s", exc)
        return web.json_response({"error": "compile_failed"}, status=500)


async def handle_authority_apply(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    text = str(body.get("text") or "").strip()[:_AUTHORITY_TEXT_MAX]
    mode = str(body.get("mode") or "shadow").lower()
    if mode not in ("off", "shadow", "enforce"):
        mode = "shadow"
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    try:
        from bot.guardian.authority import human_readable
        from bot.guardian.user_authority_store import get_user_authority_store
        env, parsed = _compile_user_envelope(text, mode)
        if parsed["unmatched"]:
            return web.json_response(
                {"ok": False, "error": "no_rules",
                 "detail": "I couldn't turn that into any limits. Try phrasings "
                           "like “only majors”, “max $500 per trade”, “$2000 a "
                           "day”, “only on bitget”."}, status=400)
        bound = get_user_authority_store().bind(tg_id, env)
        audit(system_log, f"User bound authority envelope ({mode}) {env.get('envelope_id')}",
              action="web_authority_apply", result=mode, data={"user": tg_id})
        return web.json_response({"ok": bound, "mode": mode,
                                  "human_readable": human_readable(env),
                                  "envelope_id": env.get("envelope_id")})
    except Exception as exc:
        system_log.debug("Authority apply failed: %s", exc)
        return web.json_response({"error": "apply_failed"}, status=500)


async def handle_authority_mode(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    mode = str(body.get("mode") or "").lower()
    if mode not in ("off", "shadow", "enforce"):
        return web.json_response({"error": "bad_mode"}, status=400)
    from bot.guardian.user_authority_store import get_user_authority_store
    ok = get_user_authority_store().set_mode(tg_id, mode)
    if not ok:
        return web.json_response({"error": "no_envelope"}, status=404)
    audit(system_log, f"User set authority mode {mode}", action="web_authority_mode",
          result=mode, data={"user": tg_id})
    return web.json_response({"ok": True, "mode": mode})


async def handle_authority_status(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.guardian.user_authority_store import get_user_authority_store
    from bot.guardian.authority import human_readable
    store = get_user_authority_store()
    env = store.get(tg_id)
    dec = _web_live_decision(request.app, tg_handler, tg_id)
    return web.json_response({
        "bound": env is not None,
        "mode": store.mode(tg_id),
        "human_readable": human_readable(env) if env else "",
        "envelope_id": (env or {}).get("envelope_id", ""),
        "live_ready": dec.allowed,
        "live_checklist": dec.checklist,
        "live_reason": dec.reason,
    })


async def handle_authority_revoke(request: web.Request) -> web.Response:
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.guardian.user_authority_store import get_user_authority_store
    revoked = get_user_authority_store().revoke(tg_id)
    audit(system_log, "User revoked authority envelope", action="web_authority_revoke",
          result=str(bool(revoked)), data={"user": tg_id})
    return web.json_response({"ok": True, "revoked": bool(revoked)})


# ── Intent Compiler authoring (OPERATOR only) ────────────────────────────────
#
# Web parity for the Telegram /policy compile→preview→confirm→bind loop. These
# mutate a GLOBAL engine control, so every handler re-verifies the caller is the
# operator/admin BOT-SIDE (_is_admin_id) — the Express layer's plan==='admin'
# check is defense-in-depth, not the authority (the web proposes; the bot
# decides, exactly like the stance control). Preview never writes; apply
# RECOMPILES from the text bot-side (never trusts a client-sent policy blob) and
# is the only path that binds. Every step is deterministic + fail-open.

_POLICY_TEXT_MAX = 600
_POLICY_NO_RULES_NOTE = (
    "I couldn't turn that into any rules. Try phrasings like "
    "“max 5% per trade”, “only majors”, “no shorts”, "
    "“min confidence 70%”, “stop if down 8%”.")


def _compile_policy_preview(engine, text: str) -> dict:
    """Compile NL → a previewable, clamped policy WITHOUT binding it. Pure read."""
    from bot.guardian import intent_policy as ip
    parsed = ip.compile_nl(text)
    if not parsed.get("rules"):
        return {"ok": True, "rules": [], "human_readable": "", "note": _POLICY_NO_RULES_NOTE}
    policy = ip.compile_policy({
        "mode": "shadow", "source_text": text, "label": "Operator policy",
        "rules": parsed["rules"],
    }, engine._intent_engine_caps())
    return {
        "ok": True,
        "human_readable": ip.human_readable(policy),
        "rules": policy.get("rules", []),
        "warnings": policy.get("warnings", []),
        "policy_id": policy.get("policy_id", ""),
    }


def _compile_and_bind_policy(engine, text: str, mode: str) -> dict:
    """Recompile from the TEXT (authoritative, never a client blob) with the
    chosen mode and bind it. The SOLE web path that writes a policy."""
    from bot.guardian import intent_policy as ip
    parsed = ip.compile_nl(text)
    if not parsed.get("rules"):
        return {"ok": False, "error": "no_rules"}
    policy = ip.compile_policy({
        "mode": mode, "source_text": text, "label": "Operator policy",
        "rules": parsed["rules"],
    }, engine._intent_engine_caps())
    bound = engine.write_intent_policy(policy)
    return {"ok": True, "mode": mode, "bound": bound is not None,
            "summary": engine._intent_policy_summary()}


async def _policy_op_guard(request):
    """Return (engine, tg_id, body) for an operator caller, or (None, None, err)."""
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id or not _is_admin_id(tg_handler, tg_id):
        return None, None, web.json_response({"error": "operator_only"}, status=403)
    return engine, tg_id, body


async def handle_policy_preview(request: web.Request) -> web.Response:
    engine, tg_id, body = await _policy_op_guard(request)
    if engine is None:
        return body
    text = str(body.get("text") or "").strip()[:_POLICY_TEXT_MAX]
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    try:
        return web.json_response(_compile_policy_preview(engine, text))
    except Exception as exc:
        system_log.debug("Web policy preview failed: %s", exc)
        return web.json_response({"error": "compile_failed"}, status=500)


async def handle_policy_apply(request: web.Request) -> web.Response:
    engine, tg_id, body = await _policy_op_guard(request)
    if engine is None:
        return body
    text = str(body.get("text") or "").strip()[:_POLICY_TEXT_MAX]
    mode = str(body.get("mode") or "shadow").lower()
    if mode not in ("shadow", "enforce"):
        mode = "shadow"
    if not text:
        return web.json_response({"error": "text required"}, status=400)
    try:
        result = _compile_and_bind_policy(engine, text, mode)
        if not result.get("ok"):
            return web.json_response(result, status=400)
        audit(system_log, f"Web operator bound intent policy ({mode})",
              action="web_policy_apply", result=mode)
        return web.json_response(result)
    except Exception as exc:
        system_log.debug("Web policy apply failed: %s", exc)
        return web.json_response({"error": "apply_failed"}, status=500)


async def handle_policy_mode(request: web.Request) -> web.Response:
    engine, tg_id, body = await _policy_op_guard(request)
    if engine is None:
        return body
    mode = str(body.get("mode") or "").lower()
    if mode not in ("off", "shadow", "enforce"):
        return web.json_response({"error": "bad_mode"}, status=400)
    try:
        engine.set_intent_policy_mode(mode)
    except FileNotFoundError:
        return web.json_response({"error": "no_policy"}, status=404)
    except Exception as exc:
        system_log.debug("Web policy mode failed: %s", exc)
        return web.json_response({"error": "mode_failed"}, status=500)
    audit(system_log, f"Web operator set intent policy mode {mode}",
          action="web_policy_mode", result=mode)
    return web.json_response({"ok": True, "mode": mode,
                             "summary": engine._intent_policy_summary()})


async def handle_policy_clear(request: web.Request) -> web.Response:
    engine, tg_id, body = await _policy_op_guard(request)
    if engine is None:
        return body
    removed = False
    try:
        removed = engine.clear_intent_policy()
    except Exception as exc:
        system_log.debug("Web policy clear failed: %s", exc)
    audit(system_log, "Web operator cleared intent policy",
          action="web_policy_clear", result=str(bool(removed)))
    return web.json_response({"ok": True, "removed": bool(removed)})


# ── LLM connect (WEB-1: per-user BYOK key + admin ULTRA control) ─────────────
#
# The website is the primary surface (operator rule 2026-07-20), so the LLM
# layer must be connectable from it: any user can plug in their OWN provider
# key (Fernet-encrypted in the bot's user_settings store — the same rail
# Telegram BYOK uses), and the ADMIN can flip ULTRA routing. The operator's
# Anthropic key stays admin-only throughout — a user's own key serves only
# their own chat/analysis and never enters any shared routing table.

# Providers a user may connect from the web. Local/keyless providers (ollama,
# the self-hosted runeclaw model, custom base URLs) are operator-infrastructure
# concerns and stay off this surface.
_WEB_LLM_PROVIDERS = ("openai", "anthropic", "gemini", "groq", "mistral",
                      "deepseek", "together", "openrouter", "alibaba")
_MAX_LLM_KEY_LEN = 512


# Human labels for the catalogue's qualitative cost tier — so the BYOK panel
# can show a user roughly what their own key will cost BEFORE they connect it.
# Qualitative (not live prices) on purpose: honest and low-maintenance; the
# per-provider `notes` carry the concrete $/MTok figures for those who want them.
_LLM_COST_LABEL = {
    "zero": "free (self-hosted)",
    "very_low": "very low cost",
    "low": "low cost",
    "medium": "mid cost",
    "high": "premium",
    "variable": "varies by model",
}
_LLM_SPEED_LABEL = {
    "very_fast": "very fast",
    "fast": "fast",
    "medium": "medium",
    "variable": "varies",
}


def _llm_provider_rows() -> list[dict]:
    from bot.llm.provider import PROVIDER_CATALOG, LLMProvider
    rows = []
    for name in _WEB_LLM_PROVIDERS:
        cat = PROVIDER_CATALOG.get(LLMProvider(name), {})
        cost = cat.get("cost", "")
        speed = cat.get("speed", "")
        rows.append({
            "id": name,
            "default_model": cat.get("default_model", ""),
            "free_tier": bool(cat.get("free_tier")),
            "get_key_url": cat.get("get_key_url") or "",
            "notes": cat.get("notes", ""),
            "cost": cost,
            "cost_label": _LLM_COST_LABEL.get(cost, ""),
            "speed": speed,
            "speed_label": _LLM_SPEED_LABEL.get(speed, ""),
        })
    return rows


def _llm_fingerprint(key: str) -> str:
    """Same safe-display format as LLMConfig.key_fingerprint — never the key."""
    import hashlib
    if not key:
        return ""
    return f"{key[:6]}...{hashlib.sha256(key.encode()).hexdigest()[:8]}"


async def handle_llm_status(request: web.Request) -> web.Response:
    """GET /gateway/llm?telegram_id=... — the caller's LLM connection status.
    Never returns the key itself — only a fingerprint."""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.db.models import get_user_settings, settings_user_id
    connected, provider, fingerprint = False, "", ""
    uid = settings_user_id(tg_id)
    if uid is not None:
        try:
            s = get_user_settings(uid)
            key = (s.llm_api_key or "").strip()
            if key:
                connected = True
                provider = s.llm_provider
                fingerprint = _llm_fingerprint(key)
        except Exception as exc:
            system_log.debug("LLM status read failed for %s: %s", tg_id, exc)
    from bot.llm.provider import is_ultra_mode
    resp = {
        "connected": connected,
        "provider": provider,
        "fingerprint": fingerprint,
        "per_user_enabled": bool(getattr(CONFIG.analyzer,
                                         "per_user_llm_enabled", False)),
        "providers": _llm_provider_rows(),
        "is_admin": _is_admin_id(tg_handler, tg_id),
    }
    if resp["is_admin"]:
        resp["ultra"] = is_ultra_mode()
    return web.json_response(resp)


async def handle_llm_set(request: web.Request) -> web.Response:
    """POST /gateway/llm — connect the caller's OWN LLM key.
    Body: {telegram_id, provider, api_key}. The key goes straight into the
    bot's Fernet-encrypted user_settings store; it is never logged and never
    echoed back (only a fingerprint)."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    provider_str = str(body.get("provider") or "").strip().lower()
    api_key = str(body.get("api_key") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if provider_str not in _WEB_LLM_PROVIDERS:
        return web.json_response(
            {"error": "bad_provider",
             "detail": f"provider must be one of: {', '.join(_WEB_LLM_PROVIDERS)}"},
            status=400)
    if not api_key or len(api_key) > _MAX_LLM_KEY_LEN:
        return web.json_response({"error": "bad_key",
                                  "detail": "api_key required (<=512 chars)"},
                                 status=400)
    from bot.llm.provider import BYOKManager, LLMProvider
    provider = LLMProvider(provider_str)
    if not BYOKManager._validate_key_format(provider, api_key):
        return web.json_response(
            {"error": "bad_key_format",
             "detail": "That key looks malformed — check for extra spaces or "
                       "line breaks from copy/paste, and that it's the full key."},
            status=400)
    from bot.db.models import (ensure_settings_parent, get_user_settings,
                               save_user_settings, settings_user_id)
    uid = settings_user_id(tg_id)
    if uid is None:
        return web.json_response({"error": "bad_identity"}, status=400)
    ensure_settings_parent(uid)
    s = get_user_settings(uid)
    s.llm_provider = provider_str
    s.llm_api_key = api_key
    save_user_settings(s)
    fingerprint = _llm_fingerprint(api_key)
    audit(system_log, f"Web LLM key connected: {tg_id} -> {provider_str}",
          action="web_llm_connect", result="OK",
          data={"user": tg_id, "provider": provider_str,
                "fingerprint": fingerprint})
    return web.json_response({"connected": True, "provider": provider_str,
                              "fingerprint": fingerprint})


async def handle_llm_clear(request: web.Request) -> web.Response:
    """POST /gateway/llm/clear — disconnect the caller's own LLM key."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.db.models import (ensure_settings_parent, get_user_settings,
                               save_user_settings, settings_user_id)
    uid = settings_user_id(tg_id)
    if uid is None:
        return web.json_response({"error": "bad_identity"}, status=400)
    ensure_settings_parent(uid)
    s = get_user_settings(uid)
    s.llm_api_key = ""
    save_user_settings(s)
    audit(system_log, f"Web LLM key cleared: {tg_id}",
          action="web_llm_connect", result="CLEARED", data={"user": tg_id})
    return web.json_response({"connected": False})


async def handle_news_key_save(request: web.Request) -> web.Response:
    """POST /gateway/news/key — connect the caller's own BYON news-provider key
    (NEWS-2). Body: {telegram_id, provider, api_key}. The key is validated for
    format, stored ENCRYPTED, and never echoed back (F-15)."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    provider = str(body.get("provider") or "").strip().lower()
    api_key = str(body.get("api_key") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.core import news_byon
    if not news_byon.validate_provider(provider):
        return web.json_response(
            {"error": "bad_provider",
             "detail": "provider must be one of: "
                       + ", ".join(p["id"] for p in news_byon.providers())}, status=400)
    if not news_byon.validate_key(provider, api_key):
        return web.json_response(
            {"error": "bad_key",
             "detail": "That key looks malformed — check for stray spaces or "
                       "line breaks from copy/paste, and that it's the full key."},
            status=400)
    from bot.db.models import (ensure_settings_parent, save_user_news_key,
                               settings_user_id)
    uid = settings_user_id(tg_id)
    if uid is None:
        return web.json_response({"error": "bad_identity"}, status=400)
    ensure_settings_parent(uid)
    save_user_news_key(uid, provider, api_key)
    audit(system_log, f"BYON news key connected: {tg_id} -> {provider}",
          action="news_byon_connect", result="OK",
          data={"user": tg_id, "provider": provider,
                "fingerprint": news_byon.key_fingerprint(api_key)})
    return web.json_response({"connected": True, "provider": provider,
                             "fingerprint": news_byon.key_fingerprint(api_key)})


async def handle_news_key_clear(request: web.Request) -> web.Response:
    """POST /gateway/news/key/clear — disconnect the caller's BYON news key."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.db.models import clear_user_news_key, settings_user_id
    uid = settings_user_id(tg_id)
    if uid is None:
        return web.json_response({"error": "bad_identity"}, status=400)
    clear_user_news_key(uid)
    audit(system_log, f"BYON news key cleared: {tg_id}",
          action="news_byon_connect", result="CLEARED", data={"user": tg_id})
    return web.json_response({"connected": False})


async def handle_news_key_status(request: web.Request) -> web.Response:
    """GET /gateway/news/key/status?telegram_id= — whether a BYON key is set,
    which provider, and the masked fingerprint. NEVER returns the key."""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    from bot.core import news_byon
    from bot.db.models import get_user_news_key, settings_user_id
    uid = settings_user_id(tg_id)
    provider, key = ("", "")
    if uid is not None:
        provider, key = get_user_news_key(uid)
    return web.json_response({
        "connected": bool(provider and key),
        "provider": provider or None,
        "fingerprint": news_byon.key_fingerprint(key) if key else None,
        "providers": news_byon.providers(),
    })


async def handle_llm_ultra(request: web.Request) -> web.Response:
    """POST /gateway/llm/ultra — ADMIN-only ULTRA routing toggle (web mirror
    of /ultra). Body: {telegram_id, enabled}. Refreshes the analyzer's cached
    admin tier clients so the flip reaches the brain without a restart."""
    engine = request.app["engine"]
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "admin_only"}, status=403)
    from bot.llm.provider import (BYOK, LLMConfig, LLMProvider, is_ultra_mode,
                                  set_ultra_mode)
    env_config = LLMConfig(
        provider=(LLMProvider(CONFIG.llm.provider)
                  if CONFIG.llm.provider else LLMProvider.OPENAI),
        api_key=CONFIG.llm.api_key,
        model=CONFIG.llm.model,
        base_url=CONFIG.llm.base_url,
    )
    ok, detail = set_ultra_mode(bool(body.get("enabled")),
                                BYOK.get_active_config(env_config))
    if ok:
        analyzer = getattr(engine, "analyzer", None)
        if analyzer is not None and hasattr(analyzer, "refresh_llm_client"):
            try:
                analyzer.refresh_llm_client()
            except Exception as exc:
                system_log.warning("ULTRA toggle: analyzer refresh failed: %s", exc)
        audit(system_log,
              f"ULTRA routing {'ON' if body.get('enabled') else 'OFF'} via web",
              action="ultra", result="OK",
              data={"user": tg_id, "state": bool(body.get("enabled"))})
    return web.json_response({"ok": ok, "ultra": is_ultra_mode(),
                              "detail": detail},
                             status=200 if ok else 400)


# ── Fixed-term staking (WEB-2: operator-only, double-confirm, lock END date) ─
#
# The website is the primary surface, so the /stake fixed flow ships here
# too — under the SAME hard line as Telegram: locked staking is
# OPERATOR-only behind an explicit double-confirm that shows the lock END
# date. The second confirm is enforced SERVER-side: the execute request
# must echo the exact lock end date the UI displayed; if the screen went
# stale across midnight UTC the dates diverge and the request refuses.

async def handle_staking_fixed_options(request: web.Request) -> web.Response:
    """GET /gateway/staking/fixed?telegram_id= — live lock options (ADMIN)."""
    import asyncio
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "admin_only"}, status=403)
    client = tg_handler._yield_client()
    if client is None:
        return web.json_response({"available": False,
                                  "detail": "No operator Bitget keys configured."})
    from bot.core.yield_radar import (MIN_IDLE_USD, STAKEABLE_COINS,
                                      build_report, lock_end_date)
    report = await asyncio.to_thread(
        build_report, client, tg_handler._engine_free_usdt())
    if report.error:
        return web.json_response({"available": False, "detail": report.error})
    rows = []
    for r in report.rows:
        if (r.coin not in STAKEABLE_COINS or not r.fixed_terms
                or r.stakeable_usd < MIN_IDLE_USD):
            continue
        rows.append({
            "coin": r.coin,
            "stakeable_usd": round(r.stakeable_usd, 2),
            "terms": [{"days": int(t["days"]), "apy": t["apy"],
                       "product_id": str(t["product_id"]),
                       "lock_end": lock_end_date(t["days"])}
                      for t in r.fixed_terms[:6]],
        })
    return web.json_response({
        "available": True, "rows": rows,
        "note": ("Locked funds are NOT redeemable, tradeable, or usable as "
                 "margin until the term ends. The margin reserve stays free."),
    })


async def handle_staking_fixed_execute(request: web.Request) -> web.Response:
    """POST /gateway/staking/fixed — the SECOND confirm executes.
    Body: {telegram_id, coin, product_id, days, confirm_lock_end}."""
    import asyncio
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    err = _guard_user(tg_handler, tg_id)
    if err is not None:
        return err
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "admin_only"}, status=403)
    coin = str(body.get("coin") or "").upper().strip()
    product_id = str(body.get("product_id") or "").strip()
    try:
        days = int(body.get("days"))
    except (TypeError, ValueError):
        return web.json_response({"error": "bad_days"}, status=400)
    if not coin or not product_id or days <= 0:
        return web.json_response({"error": "bad_request"}, status=400)
    from bot.core.yield_radar import execute_stake_fixed, lock_end_date
    expected_end = lock_end_date(days)
    if str(body.get("confirm_lock_end") or "").strip() != expected_end:
        # The confirm MUST restate the lock end date the user saw. A stale
        # screen (midnight rollover) re-shows rather than silently locking
        # to a different date than the one confirmed.
        return web.json_response(
            {"error": "lock_end_mismatch", "expected_lock_end": expected_end},
            status=409)
    client = tg_handler._yield_client()
    if client is None:
        return web.json_response({"error": "no_operator_keys"}, status=503)
    res = await asyncio.to_thread(
        execute_stake_fixed, client, coin, product_id, days,
        tg_handler._engine_free_usdt())
    audit(system_log, f"Earn FIXED lock {coin} {days}d via web double-confirm",
          action="earn_action_fixed", result="OK" if res.ok else "FAIL",
          data={"user": tg_id, "coin": coin, "days": days,
                "detail": res.message})
    return web.json_response({"ok": res.ok, "detail": res.message},
                             status=200 if res.ok else 400)


# ── App factory ──────────────────────────────────────────────────────────────

async def handle_web3_execute(request: web.Request) -> web.Response:
    """WEB3-LIVE-EXEC slice 1 — admin-only, envelope-gated DRY-RUN PREVIEW of an
    on-chain action. It NEVER signs or broadcasts: it proves the full gate +
    Authority-Envelope authorization path and returns a preview (the way the
    proof-of-PnL anchor dry-run does). Signing and broadcast ship in a later,
    separately-gated slice — this handler must never call a signer or send a tx.

    The on-chain action is authorized as a TRANSFER: value leaving the account to
    a destination, so the envelope must have withdraw_allowed AND the destination
    (router/recipient) on its allowlist — the correct discipline for any real
    outflow, exercised here before a single wei can ever move.
    """
    import time as _time
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    # Admin-only in this phase.
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "web3 execution is admin-only"}, status=403)

    from bot.web import web3_exec_gate as _gate
    network = str(body.get("network") or "sepolia")
    broadcast = bool(body.get("broadcast"))
    action_in = {
        "side": str(body.get("side") or "swap"),
        "from_token": str(body.get("from_token") or "").upper(),
        "to_token": str(body.get("to_token") or "").upper(),
        "amount_usd": body.get("amount_usd"),
        "dest": str(body.get("dest") or ""),
    }

    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        _store = get_user_authority_store()
        enforcing = bool(_store.is_enforcing(tg_id))
    except Exception:
        _store, enforcing = None, False

    # 1) Preconditions (feature flag, admin, testnet-first, preview-only, envelope).
    decision = _gate.evaluate(is_admin=True, network=network,
                              envelope_enforcing=enforcing, broadcast=broadcast)
    if not decision.allowed:
        return web.json_response({"error": "web3_gate_denied", "reason": decision.reason,
                                  "checklist": decision.checklist}, status=403)

    # 2) The Authority Envelope authorizes THIS specific outflow (transfer).
    env_id = None
    try:
        from bot.guardian.authority import authorize
        env = _store.get(tg_id) if _store else None
        notional = None
        try:
            if action_in["amount_usd"] is not None:
                notional = float(action_in["amount_usd"])
        except (TypeError, ValueError):
            notional = None
        auth_action = {"kind": "transfer",
                       "asset": action_in["to_token"] or action_in["from_token"],
                       "notional_usd": notional, "dest": action_in["dest"] or None}
        result = authorize(env, auth_action, now_ts=_time.time(), spent_today_usd=0.0)
        if result.get("decision") != "allow":
            return web.json_response({"error": "authority_denied",
                                      "reasons": list(result.get("reasons") or ["not authorized"])},
                                     status=403)
        env_id = result.get("envelope_id")
    except Exception:
        return web.json_response({"error": "authority_check_failed"}, status=403)

    # 3) DRY-RUN PREVIEW ONLY — no signer exists and none is called here.
    net = decision.network or {}
    audit(system_log, f"Web3 exec PREVIEW (admin) {action_in['side']} on {network}",
          action="web3_exec_preview", result="OK", data={"network": network})
    # Guardian pre-trade review: record this proposed action so a human can
    # review it (and TIGHTEN the envelope) before any future signer slice acts on
    # it. Observe-only — recording never blocks or alters this preview.
    try:
        from bot.guardian.review_queue import get_review_queue
        get_review_queue().record({"user_id": tg_id, "kind": "web3_transfer",
                                    "network": network, "action": action_in,
                                    "envelope_id": env_id, "ts": _time.time()})
    except Exception:
        pass
    return web.json_response({
        "dry_run": True,
        "broadcast": False,
        "network": net.get("label"),
        "chain_id": net.get("chain_id"),
        "testnet": net.get("testnet"),
        "action": action_in,
        "envelope": {"id": env_id, "mode": (_store.mode(tg_id) if _store else "off")},
        "estimate": {"note": "on-chain route quote + gas estimate arrive with the "
                             "signer slice; this preview proves the gate + envelope path"},
        "note": "Preview only — RUNECLAW did not sign or broadcast anything. Real "
                "signing ships in a later, separately-gated, still admin-only, "
                "still envelope-enforced slice.",
    })


async def handle_guardian_review(request: web.Request) -> web.Response:
    """Admin-only, READ-ONLY view of the pre-trade review queue: every proposed
    high-risk action (currently the on-chain execution preview) recorded for a
    human to review before any signer slice acts on it."""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "review queue is admin-only"}, status=403)
    try:
        from bot.guardian.review_queue import get_review_queue
        q = get_review_queue()
        return web.json_response({"read_only": True, "pending": q.pending_count(),
                                  "entries": q.list(limit=50)})
    except Exception:
        return web.json_response({"error": "review queue unavailable"}, status=502)


async def handle_guardian_review_tighten(request: web.Request) -> web.Response:
    """Admin-only: TIGHTEN a target user's Authority Envelope. The only mutation
    this surface exposes — and it can only make the envelope MORE restrictive
    (property-tested), never authorize or loosen. Marks the user's pending review
    entries reviewed. No signer, no broadcast — this only narrows authority."""
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()          # the admin acting
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "tightening is admin-only"}, status=403)
    target = str(body.get("target_user") or "").strip()
    if not target:
        return web.json_response({"error": "target_user required"}, status=400)
    spec = body.get("tighten") if isinstance(body.get("tighten"), dict) else {}
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        from bot.guardian.review_queue import tighten_envelope, get_review_queue
        store = get_user_authority_store()
        cur = store.get(target)
        if not cur:
            return web.json_response({"error": "no authority envelope bound for that user"},
                                     status=404)
        new_env = tighten_envelope(cur, spec)
        store.bind(target, new_env)
        reviewed = get_review_queue().mark_reviewed(target, note="envelope tightened")
        audit(system_log, f"Guardian envelope TIGHTENED for {target} by admin {tg_id}",
              action="guardian_tighten", result="OK",
              data={"target": target, "envelope_id": new_env.get("envelope_id")})
        return web.json_response({"ok": True, "envelope_id": new_env.get("envelope_id"),
                                  "reviewed": reviewed,
                                  "envelope": {"allowed_venues": new_env.get("allowed_venues"),
                                               "max_notional_per_trade_usd": new_env.get("max_notional_per_trade_usd"),
                                               "max_notional_daily_usd": new_env.get("max_notional_daily_usd"),
                                               "withdraw_allowed": new_env.get("withdraw_allowed"),
                                               "revoked": new_env.get("revoked")}})
    except Exception:
        # F-15: never leak an exception string (it can carry secrets) to the caller.
        return web.json_response({"error": "tightening failed"}, status=400)


async def handle_web3_sign(request: web.Request) -> web.Response:
    """WEB3-LIVE-EXEC slice 2 — admin-only, TESTNET-ONLY live SIGN + broadcast of
    a native-value transfer to an envelope-allowlisted destination. Triple-gated
    default-OFF (WEB3_LIVE_EXEC_ENABLED + WEB3_LIVE_EXEC_SIGN_ENABLED + a
    configured signer key + the eth-account library + an enforcing envelope), and
    still run through the Authority Envelope authorize() as a transfer. Mainnet is
    refused here regardless of any flag. NEVER returns or logs the signing key;
    F-15 on every error path."""
    import time as _time
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "web3 signing is admin-only"}, status=403)

    from bot.web import web3_signer as _signer
    network = str(body.get("network") or "sepolia")
    dest = str(body.get("to") or body.get("dest") or "").strip()
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        _store = get_user_authority_store()
        enforcing = bool(_store.is_enforcing(tg_id))
    except Exception:
        _store, enforcing = None, False

    # 1) Signing preconditions — testnet-only, own flag, key + library present.
    decision = _signer.evaluate_sign(is_admin=True, network=network,
                                     envelope_enforcing=enforcing)
    if not decision.allowed:
        return web.json_response({"error": "web3_sign_denied", "reason": decision.reason,
                                  "checklist": decision.checklist}, status=403)

    # 2) The Authority Envelope authorizes THIS outflow (transfer to dest).
    try:
        notional = float(body.get("amount_usd")) if body.get("amount_usd") is not None else None
    except (TypeError, ValueError):
        notional = None
    try:
        from bot.guardian.authority import authorize
        env = _store.get(tg_id) if _store else None
        result = authorize(env, {"kind": "transfer", "asset": str(body.get("asset") or "ETH"),
                                 "notional_usd": notional, "dest": dest or None},
                           now_ts=_time.time(), spent_today_usd=0.0)
        if result.get("decision") != "allow":
            return web.json_response({"error": "authority_denied",
                                      "reasons": list(result.get("reasons") or ["not authorized"])},
                                     status=403)
        env_id = result.get("envelope_id")
    except Exception:
        return web.json_response({"error": "authority_check_failed"}, status=403)

    # 3) Sign (audited eth-account) + broadcast to the configured testnet RPC.
    try:
        value_wei = int(body.get("value_wei") or 0)
        nonce = int(body.get("nonce"))
    except (TypeError, ValueError):
        return web.json_response({"error": "value_wei and nonce are required integers"},
                                 status=400)
    # Prefer the prepared EIP-1559 fees (from /web3/sign/prepare) when present; the
    # signer falls back to its safe defaults otherwise.
    _sign_kw = {"gas": int(body.get("gas") or 21000)}
    for _k, _bk in (("max_fee_wei", "max_fee_wei"), ("max_priority_wei", "max_priority_wei")):
        try:
            if body.get(_bk) is not None:
                _sign_kw[_k] = int(body.get(_bk))
        except (TypeError, ValueError):
            pass
    signed = _signer.build_and_sign(network=network, to=dest, value_wei=value_wei,
                                    nonce=nonce, **_sign_kw)
    if not signed.get("ok"):
        return web.json_response({"error": "sign_failed", "reason": signed.get("error")},
                                 status=400)
    net = decision.network or {}
    bcast = await _signer.broadcast(signed["raw"], _signer.rpc_url_for(network),
                                    net.get("chain_id"))

    # 4) Record to the Guardian review queue (fail-safe — never blocks the send).
    try:
        from bot.guardian.review_queue import get_review_queue
        get_review_queue().record({"user_id": tg_id, "kind": "web3_sign", "network": network,
                                   "action": {"side": "transfer", "to": dest,
                                              "amount_usd": notional,
                                              "tx_hash": signed.get("tx_hash"),
                                              "broadcast": bool(bcast.get("ok"))},
                                   "envelope_id": env_id, "ts": _time.time()})
    except Exception:
        pass

    audit(system_log, f"Web3 SIGN (admin) testnet {network} -> {dest[:10]}",
          action="web3_sign", result="OK" if bcast.get("ok") else "SIGNED",
          data={"network": network, "broadcast": bool(bcast.get("ok"))})
    _txh = bcast.get("tx_hash") or signed.get("tx_hash")
    from bot.web.web3_exec_gate import explorer_tx_url as _explorer_tx_url
    return web.json_response({
        "signed": True,
        "broadcast": bool(bcast.get("ok")),
        "network": net.get("label"),
        "chain_id": net.get("chain_id"),
        "testnet": net.get("testnet"),
        "from": signed.get("from"),
        "to": dest,
        "tx_hash": _txh,
        # One-click on-chain record: the block-explorer tx URL (empty when the
        # network/hash can't build a valid link).
        "explorer_url": _explorer_tx_url(network, _txh) if bcast.get("ok") else "",
        "envelope": {"id": env_id},
        "note": bcast.get("error") or "Signed and broadcast to testnet. Testnet-only in "
                "this slice — mainnet signing is a separate, later, separately-gated slice.",
    })


async def handle_web3_sign_status(request: web.Request) -> web.Response:
    """WEB3-LIVE-EXEC slice 2 — admin-only, read-only signer STATUS for the web UI.
    Returns the signing flags, whether the library + key are present, the signer's
    PUBLIC address, and per-testnet RPC readiness. NEVER returns the signing key."""
    tg_handler = request.app["tg_handler"]
    tg_id = str(request.query.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "web3 signing is admin-only"}, status=403)
    from bot.web import web3_signer as _signer
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        enforcing = bool(get_user_authority_store().is_enforcing(tg_id))
    except Exception:
        enforcing = False
    status = _signer.signer_status()
    status["envelope_enforcing"] = enforcing
    return web.json_response(status)


async def handle_web3_prepare(request: web.Request) -> web.Response:
    """WEB3-LIVE-EXEC slice 2 — admin-only, TESTNET-ONLY tx PREPARE: auto-fetch the
    next nonce + EIP-1559 gas fees for the signer address on a testnet, so the send
    form never needs a hand-computed nonce. Read-only RPC; never signs, never
    touches the key. Runs the same signing gate first (fail-closed)."""
    import time as _time
    tg_handler = request.app["tg_handler"]
    body = await _json_body(request)
    tg_id = str(body.get("telegram_id") or "").strip()
    if not tg_id:
        return web.json_response({"error": "telegram_id required"}, status=400)
    if not _is_admin_id(tg_handler, tg_id):
        return web.json_response({"error": "web3 signing is admin-only"}, status=403)

    from bot.web import web3_signer as _signer
    network = str(body.get("network") or "sepolia")
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        enforcing = bool(get_user_authority_store().is_enforcing(tg_id))
    except Exception:
        enforcing = False
    # Same fail-closed gate as the sign path — prepare is a step toward signing.
    decision = _signer.evaluate_sign(is_admin=True, network=network,
                                     envelope_enforcing=enforcing)
    if not decision.allowed:
        return web.json_response({"error": "web3_sign_denied", "reason": decision.reason,
                                  "checklist": decision.checklist}, status=403)
    addr = _signer.signer_address()
    prep = await _signer.prepare_tx(network=network, address=addr or "")
    if not prep.get("ok"):
        return web.json_response({"error": "prepare_failed", "reason": prep.get("error")},
                                 status=400)
    net = decision.network or {}
    audit(system_log, f"Web3 PREPARE (admin) testnet {network}", action="web3_prepare",
          result="OK", data={"network": network, "nonce": prep.get("nonce")})
    return web.json_response({
        "ok": True, "network": net.get("label"), "chain_id": net.get("chain_id"),
        "testnet": net.get("testnet"), "from": addr,
        "nonce": prep.get("nonce"), "gas": prep.get("gas"),
        "max_fee_wei": prep.get("max_fee_wei"),
        "max_priority_wei": prep.get("max_priority_wei"),
        "base_fee_wei": prep.get("base_fee_wei"),
        "prepared_ts": _time.time(),
    })


def build_gateway(engine, tg_handler) -> web.Application:
    """Build the /gateway sub-app. Caller mounts it under the dashboard app."""
    app = web.Application(middlewares=[secret_middleware])
    app["engine"] = engine
    app["tg_handler"] = tg_handler
    app["proposers"] = {}
    app.router.add_post("/chat", handle_chat)
    app.router.add_post("/chat/public", handle_public_chat)
    app.router.add_post("/contract/studio", handle_contract_studio)
    app.router.add_post("/contract/compile", handle_contract_compile)
    app.router.add_post("/contract/deploy", handle_contract_deploy)
    app.router.add_post("/cross/plan", handle_cross_plan)
    app.router.add_get("/chat/history", handle_chat_history)
    # AI-4: admin-only cited web research enrichment for the coin dossier.
    app.router.add_post("/research/web", handle_research_web)
    app.router.add_get("/portfolio", handle_portfolio)
    app.router.add_get("/positions", handle_positions)
    app.router.add_get("/networth", handle_networth)
    app.router.add_get("/holdings", handle_holdings)
    app.router.add_get("/sentry", handle_sentry)
    app.router.add_get("/news", handle_news)
    app.router.add_get("/proofofpnl", handle_proofofpnl)
    app.router.add_get("/public/proofofpnl", handle_proofofpnl_public)
    app.router.add_get("/public/leaderboard", handle_leaderboard_public)
    app.router.add_get("/share-card", handle_share_card)
    app.router.add_get("/public/agent/{address}", handle_agent_card_public)
    app.router.add_post("/idleyield", handle_idle_yield)
    # Fixed-term staking (WEB-2): operator-only, double-confirm w/ lock end.
    app.router.add_get("/staking/fixed", handle_staking_fixed_options)
    app.router.add_post("/staking/fixed", handle_staking_fixed_execute)
    # WEB3-LIVE-EXEC slice 1: admin-only, envelope-gated on-chain PREVIEW (no
    # signer, no broadcast — the safety spine for future live signing).
    app.router.add_post("/web3/execute", handle_web3_execute)
    # WEB3-LIVE-EXEC slice 2: admin-only, TESTNET-ONLY live sign + broadcast, plus
    # the read-only signer status and the nonce/gas prepare step that drive the UI.
    app.router.add_post("/web3/sign", handle_web3_sign)
    app.router.add_get("/web3/sign/status", handle_web3_sign_status)
    app.router.add_post("/web3/sign/prepare", handle_web3_prepare)
    # Guardian pre-trade review queue: admin-only read + tighten-only mutation.
    app.router.add_get("/guardian/review", handle_guardian_review)
    app.router.add_post("/guardian/review/tighten", handle_guardian_review_tighten)
    # LLM connect (WEB-1): per-user BYOK key + admin ULTRA toggle.
    app.router.add_get("/llm", handle_llm_status)
    app.router.add_post("/llm", handle_llm_set)
    app.router.add_post("/llm/clear", handle_llm_clear)
    app.router.add_post("/llm/ultra", handle_llm_ultra)
    # BYON news (NEWS-2): per-user paid news-provider key, enriches THEIR feed.
    app.router.add_post("/news/key", handle_news_key_save)
    app.router.add_post("/news/key/clear", handle_news_key_clear)
    app.router.add_get("/news/key/status", handle_news_key_status)
    # Authority Envelope authoring (per-user, self-serve; _guard_user-gated).
    app.router.add_post("/authority/preview", handle_authority_preview)
    app.router.add_post("/authority/apply", handle_authority_apply)
    app.router.add_post("/authority/mode", handle_authority_mode)
    app.router.add_get("/authority/status", handle_authority_status)
    app.router.add_post("/authority/revoke", handle_authority_revoke)
    app.router.add_post("/trade/propose", handle_trade_propose)
    app.router.add_post("/trade/confirm", handle_trade_confirm)
    app.router.add_get("/trade/live_mode", handle_trade_live_mode)
    app.router.add_post("/trade/cancel", handle_trade_cancel)
    app.router.add_post("/trade/copilot", handle_trade_copilot)
    # Intent Compiler authoring (operator-only; _is_admin_id-gated per handler).
    app.router.add_post("/policy/preview", handle_policy_preview)
    app.router.add_post("/policy/apply", handle_policy_apply)
    app.router.add_post("/policy/mode", handle_policy_mode)
    app.router.add_post("/policy/clear", handle_policy_clear)
    return app
