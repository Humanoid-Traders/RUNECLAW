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

    if not tg_id or not text:
        return web.json_response({"error": "telegram_id and text required"}, status=400)
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

    # Fallback: LLM chat — same append-around-call pattern as _handle_message.
    from bot.nlp.sanitize import sanitize_chat_input
    tg_handler.conversations.append(tg_id, "user", text,
                                    metadata={"intent": intent.skill or "chat",
                                              "surface": "web"})
    # is_admin MUST reflect the caller's real role: resolve_tier_config's
    # non-admin guard (operator Anthropic key stays admin-only) and the
    # fallback-chain gate in _llm_chat both key off this flag.
    answer, meta = await tg_handler._llm_chat(
        sanitize_chat_input(text), user_id=tg_id, user_name=name,
        is_admin=_is_admin_id(tg_handler, tg_id),
        profile_note=profile_note, return_meta=True)
    tg_handler.conversations.append(tg_id, "assistant", answer,
                                    metadata={"surface": "web"})
    # Model transparency: the web renders a small caption showing WHICH
    # model answered — the visible face of tier routing (and of a runeclaw
    # promotion via /settier).
    return web.json_response({"reply_html": answer, "intent": "chat",
                              "model": (meta or {}).get("model", ""),
                              "provider": (meta or {}).get("provider", "")})


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

    from bot.nlp.sanitize import sanitize_chat_input
    answer = await tg_handler._llm_chat(
        sanitize_chat_input(text), user_id="", user_name="",
        is_admin=False, public=True)
    return web.json_response({"reply_html": answer, "intent": "chat"})


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
        "order_type": "limit",
        "mode": mode,
        "live_allowed": live_allowed,
        "live_reason": live_reason,
    }


def _propose_from_text(app, tg_handler, engine, tg_id: str, text: str,
                       name: str = "") -> web.Response:
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
        idea = build_manual_idea(direction, symbol, entry, sl, tp)
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
                              name=name)


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


# ── App factory ──────────────────────────────────────────────────────────────

def build_gateway(engine, tg_handler) -> web.Application:
    """Build the /gateway sub-app. Caller mounts it under the dashboard app."""
    app = web.Application(middlewares=[secret_middleware])
    app["engine"] = engine
    app["tg_handler"] = tg_handler
    app["proposers"] = {}
    app.router.add_post("/chat", handle_chat)
    app.router.add_post("/chat/public", handle_public_chat)
    app.router.add_get("/chat/history", handle_chat_history)
    app.router.add_get("/portfolio", handle_portfolio)
    app.router.add_get("/networth", handle_networth)
    app.router.add_get("/holdings", handle_holdings)
    app.router.add_post("/idleyield", handle_idle_yield)
    # Authority Envelope authoring (per-user, self-serve; _guard_user-gated).
    app.router.add_post("/authority/preview", handle_authority_preview)
    app.router.add_post("/authority/apply", handle_authority_apply)
    app.router.add_post("/authority/mode", handle_authority_mode)
    app.router.add_get("/authority/status", handle_authority_status)
    app.router.add_post("/authority/revoke", handle_authority_revoke)
    app.router.add_post("/trade/propose", handle_trade_propose)
    app.router.add_post("/trade/confirm", handle_trade_confirm)
    app.router.add_post("/trade/cancel", handle_trade_cancel)
    app.router.add_post("/trade/copilot", handle_trade_copilot)
    # Intent Compiler authoring (operator-only; _is_admin_id-gated per handler).
    app.router.add_post("/policy/preview", handle_policy_preview)
    app.router.add_post("/policy/apply", handle_policy_apply)
    app.router.add_post("/policy/mode", handle_policy_mode)
    app.router.add_post("/policy/clear", handle_policy_clear)
    return app
