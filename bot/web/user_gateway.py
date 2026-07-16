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

    # Manual trade via natural language — same intercept as _handle_message:
    # "buy SOL 71 sl 70 tp 76" proposes a pending trade (never executes).
    trade_text = text.lower().strip()
    if trade_text.startswith("trade "):
        trade_text = trade_text[6:].strip()
    if (any(trade_text.startswith(p) for p in ("buy ", "long ", "short ", "sell "))
            and " sl " in trade_text):
        return _propose_from_text(request.app, tg_handler, engine, tg_id,
                                  trade_text, name=name)

    # Intent routing — same threshold as Telegram (confidence >= 0.8).
    intent = tg_handler.intent_router.classify_rules(text)
    if intent.matched and intent.confidence >= 0.8:
        skill = tg_handler.registry.get(intent.skill)
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


def _trade_mode(tg_handler, tg_id: str) -> tuple[str, bool]:
    """(mode, live_allowed) exactly as the Telegram gate decides it.

    Web-only identities are structurally paper-only: even a tampered
    users.json entry (web:N with role=admin / can_trade_live=true) never
    yields LIVE here.
    """
    if _is_web_id(tg_id):
        return "PAPER", False
    live_allowed = bool(_is_admin_id(tg_handler, tg_id)
                        or tg_handler._can_trade_live(tg_id))
    mode = "LIVE" if (CONFIG.is_live() and live_allowed) else "PAPER"
    return mode, live_allowed


def _idea_payload(app, tg_handler, tg_id: str, idea, margin_usd) -> dict:
    entry, sl, tp = idea.entry_price, idea.stop_loss, idea.take_profit
    mode, live_allowed = _trade_mode(tg_handler, tg_id)
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
    # Web-only identities can NEVER confirm in live mode. This check runs
    # BEFORE the _is_admin_id bypass on purpose: a tampered users.json entry
    # (web:N with role=admin) must not open a live path.
    if CONFIG.is_live() and _is_web_id(tg_id):
        return web.json_response({"error": "live_not_enabled"}, status=403)
    # Live gate — same H-18 check as the Telegram confirm path.
    if CONFIG.is_live() and not _is_admin_id(tg_handler, tg_id):
        if not tg_handler._can_trade_live(tg_id):
            return web.json_response({"error": "live_not_enabled"}, status=403)
    result = await engine.confirm_trade(trade_id, user_id=tg_id)
    request.app["proposers"].pop(trade_id, None)
    audit(system_log, f"Web trade confirm: {trade_id}",
          action="web_trade_confirm", result="OK", data={"user": tg_id})
    return web.json_response({"result_html": result})


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
    app.router.add_post("/trade/propose", handle_trade_propose)
    app.router.add_post("/trade/confirm", handle_trade_confirm)
    app.router.add_post("/trade/cancel", handle_trade_cancel)
    return app
