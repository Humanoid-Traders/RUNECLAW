"""
RUNECLAW — Live Web Dashboard API Server
=========================================
Thin aiohttp layer exposing engine state as JSON endpoints.
Runs alongside the Telegram bot on the same asyncio loop.

F-02 FIX: Bearer token authentication on all /api/* endpoints.
F-03 FIX: CORS restricted to configured origin (not wildcard).
"""

from __future__ import annotations

import json
import hmac
import os
import pathlib
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

# Lazy imports — engine may not be available during module load
_ENGINE = None

# F-02 FIX: Dashboard token from environment. If empty, API endpoints
# return 403 with a setup instruction (fail-closed).
_DASHBOARD_TOKEN: str = os.environ.get("DASHBOARD_TOKEN", "")

# F-03 FIX: Allowed CORS origin. Default to same-origin (empty = no CORS headers).
_CORS_ORIGIN: str = os.environ.get("DASHBOARD_CORS_ORIGIN", "")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(obj: Any) -> dict:
    """Convert dataclass/model to dict safely."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {}


# ── API Handlers ─────────────────────────────────────────────

async def handle_state(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    data: dict[str, Any] = {"timestamp": _ts()}

    # Portfolio (combined from all users)
    try:
        if engine.user_portfolios.all_portfolios():
            snap = engine.user_portfolios.combined_snapshot()
        else:
            snap = engine.portfolio.snapshot()
        data["portfolio"] = _safe_dict(snap)
    except Exception:
        data["portfolio"] = {}

    # Risk
    try:
        data["risk"] = {
            "circuit_breaker_active": engine.risk.circuit_breaker_active,
            "consecutive_losses": engine.risk.consecutive_losses,
            **engine.risk.stats,
            "rejection_history": list(engine.risk.rejection_history[-10:]),
        }
    except Exception:
        data["risk"] = {}

    # Engine
    try:
        state_name = engine.state.value if hasattr(engine.state, "value") else str(engine.state)
        history = []
        for t in (engine.state_history or [])[-8:]:
            history.append({
                "from": t.from_state.value if hasattr(t.from_state, "value") else str(t.from_state),
                "to": t.to_state.value if hasattr(t.to_state, "value") else str(t.to_state),
                "reason": getattr(t, "reason", ""),
            })
        # RC-AUD-016: report the REAL trading mode, not a hardcoded True.
        # A hardcoded "simulation_mode": True made the dashboard show paper mode
        # even while trading live with real capital.
        try:
            from bot.config import CONFIG as _engine_cfg
            _sim_mode = not _engine_cfg.is_live()
        except Exception:
            _sim_mode = True  # fail safe: default to showing simulation
        data["engine"] = {
            "state": state_name,
            "scan_interval": getattr(engine, "_scan_interval", 60),
            "pending_ideas": len(getattr(engine, "pending_ideas", [])),
            "simulation_mode": _sim_mode,
            "state_history": history,
        }
    except Exception:
        data["engine"] = {"state": "UNKNOWN"}

    # LLM Tiers
    try:
        from bot.llm.provider import DEFAULT_TIER_ROUTING, LLMTier
        tiers = {}
        for tier in LLMTier:
            route = DEFAULT_TIER_ROUTING.get(tier, {})
            provider = route.get("provider")
            tiers[tier.value] = {
                "provider": provider.value if hasattr(provider, "value") else str(provider),
                "model": route.get("model", ""),
                "reason": route.get("reason", ""),
            }
        data["llm_tiers"] = tiers
    except Exception:
        data["llm_tiers"] = {}

    # Cost
    try:
        cost_snap = engine.cost.snapshot()
        data["cost"] = _safe_dict(cost_snap)
    except Exception:
        data["cost"] = {}

    return web.json_response(data)


async def handle_positions(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    positions = []
    try:
        # Gather positions from all user portfolios
        for uid, port in engine.user_portfolios.all_portfolios().items():
            trailing = port.get_trailing_status() if hasattr(port, 'get_trailing_status') else {}
            for pos in port.open_positions:
                d = _safe_dict(pos)
                d["user_id"] = uid
                tid = getattr(pos, "trade_id", "")
                if tid in trailing:
                    d.update(trailing[tid])
                positions.append(d)
    except Exception:
        pass
    return web.json_response({"positions": positions})


async def handle_signals(request: web.Request) -> web.Response:
    engine = request.app["engine"]
    signals = []
    try:
        tracker = getattr(engine, "signal_tracker", None)
        if tracker:
            all_stats = tracker.get_all_pair_stats()
            for symbol, stats in all_stats.items():
                signals.append({"symbol": symbol, **stats})
    except Exception:
        pass

    # Also include recent trade history
    trades = []
    try:
        for uid, port in engine.user_portfolios.all_portfolios().items():
            for t in (port.trade_history or [])[-20:]:
                trades.append(_safe_dict(t))
    except Exception:
        pass

    return web.json_response({"signals": signals, "trades": trades})


async def handle_index(request: web.Request) -> web.Response:
    """Serve the dashboard HTML."""
    html_path = pathlib.Path(__file__).parent / "dashboard.html"
    if html_path.exists():
        return web.FileResponse(html_path, content_type="text/html")
    return web.Response(text="Dashboard HTML not found", status=404)


# ── Operational endpoints (liveness / readiness / metrics) ───────────
# Unauthenticated by design: auth_middleware only guards /api/*, and these are
# standard probe/scrape endpoints. /metrics exposes ONLY non-financial
# operational counters (uptime, connectivity, latency, counts) — never equity,
# PnL or any per-user data — so it is safe to expose to a scraper.

def _is_ready(snap) -> bool:
    """Readiness = exchange reachable and health not CRITICAL."""
    return bool(getattr(snap, "exchange_connected", False)) and \
        getattr(snap, "status", "CRITICAL") != "CRITICAL"


async def handle_health(request: web.Request) -> web.Response:
    """Liveness: the web server is up and serving. Always 200 (it answered)."""
    return web.json_response({"status": "ok", "timestamp": _ts()})


async def handle_ready(request: web.Request) -> web.Response:
    """Readiness: 200 when the engine can trade, 503 otherwise.

    Fails CLOSED — if health can't be determined the bot is reported NOT ready,
    so a load balancer / orchestrator never routes to a half-up instance.
    """
    engine = request.app["engine"]
    try:
        snap = engine.health.snapshot()
        ready = _is_ready(snap)
        body = {
            "ready": ready,
            "status": getattr(snap, "status", "UNKNOWN"),
            "exchange_connected": bool(getattr(snap, "exchange_connected", False)),
            "uptime_seconds": getattr(snap, "uptime_seconds", 0.0),
        }
        return web.json_response(body, status=200 if ready else 503)
    except Exception as exc:
        return web.json_response(
            {"ready": False, "error": str(exc)[:120]}, status=503)


def _render_prometheus(engine) -> str:
    """Hand-rolled Prometheus text exposition (no prometheus_client dependency).

    Only non-financial operational signals — safe for an unauthenticated scrape.
    """
    lines: list[str] = []

    def metric(name: str, value, help_text: str, mtype: str = "gauge") -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {mtype}")
        lines.append(f"{name} {value}")

    metric("runeclaw_up", 1, "1 if the bot web server is serving.")
    try:
        s = engine.health.snapshot()
        metric("runeclaw_ready", int(_is_ready(s)), "1 if the bot is ready to trade.")
        metric("runeclaw_uptime_seconds", s.uptime_seconds, "Process uptime in seconds.", "counter")
        metric("runeclaw_exchange_connected", int(bool(s.exchange_connected)), "1 if the exchange is connected.")
        metric("runeclaw_ws_connected", int(bool(s.ws_connected)), "1 if the market-data websocket is connected.")
        metric("runeclaw_api_latency_ms", s.api_latency_ms, "Rolling average API latency (ms).")
        metric("runeclaw_api_latency_p99_ms", s.api_latency_p99_ms, "p99 API latency (ms).")
        metric("runeclaw_api_error_rate_pct", s.error_rate_pct, "API error rate over the window (percent).")
        metric("runeclaw_api_calls_total", s.total_api_calls, "Total API calls observed.", "counter")
        metric("runeclaw_api_errors_total", s.total_errors, "Total API errors observed.", "counter")
    except Exception:
        pass
    try:
        metric("runeclaw_open_positions", len(list(engine.portfolio.open_positions)),
               "Number of currently open positions.")
    except Exception:
        pass
    try:
        metric("runeclaw_circuit_breaker_active", int(bool(engine.risk.circuit_breaker_active)),
               "1 if the risk circuit breaker is tripped.")
        metric("runeclaw_consecutive_losses", engine.risk.consecutive_losses,
               "Consecutive losing trades.")
        rstats = engine.risk.stats
        if isinstance(rstats, dict):
            if "total_checks" in rstats:
                metric("runeclaw_risk_checks_total", rstats["total_checks"],
                       "Total risk evaluations.", "counter")
            if "total_rejections" in rstats:
                metric("runeclaw_risk_rejections_total", rstats["total_rejections"],
                       "Total risk rejections.", "counter")
    except Exception:
        pass

    return "\n".join(lines) + "\n"


async def handle_metrics(request: web.Request) -> web.Response:
    """Prometheus exposition of operational metrics (text/plain)."""
    engine = request.app["engine"]
    return web.Response(text=_render_prometheus(engine), content_type="text/plain")


# ── Auth Middleware (F-02 FIX) ────────────────────────────────

@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Require Bearer token on /api/* endpoints.

    F-02 FIX: All API endpoints now require DASHBOARD_TOKEN to be set
    and provided via Authorization header.  The index page (/) is
    served without auth so the dashboard HTML can load.

    RC-AUD-017: the /api/* handlers return AGGREGATE multi-user state —
    every user's positions, equity, rejection history, LLM routing, and
    cost (see handle_state/handle_positions/handle_signals). There is no
    per-request auth-identity plumbing to scope responses to a single
    operator, so this surface MUST stay (a) token-gated and (b) bound to a
    trusted network. The "no DASHBOARD_TOKEN configured" branch below is
    therefore fail-closed (returns 403, never serves data), and bot/main.py
    binds the dashboard to a configurable host (DASHBOARD_BIND_HOST) that
    should be localhost or a private/docker network, never the public
    internet. Do NOT relax either control without adding real per-user
    request scoping first.
    """
    if request.path.startswith("/api/"):
        if not _DASHBOARD_TOKEN:
            # Fail-closed: with no token there is no way to authenticate the
            # caller, so refuse rather than expose aggregate multi-user state.
            return web.json_response(
                {"error": "DASHBOARD_TOKEN not configured. Set it in .env to enable the API."},
                status=403,
            )
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()
        if not token or not hmac.compare_digest(token, _DASHBOARD_TOKEN):
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


# ── CORS Middleware (F-03 FIX) ────────────────────────────────

@web.middleware
async def cors_middleware(request: web.Request, handler):
    """Restricted CORS — only the configured origin is allowed.

    F-03 FIX: Replaced wildcard `*` with explicit origin from
    DASHBOARD_CORS_ORIGIN env var.  If not set, no CORS headers
    are emitted (same-origin only).
    """
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    if _CORS_ORIGIN:
        resp.headers["Access-Control-Allow-Origin"] = _CORS_ORIGIN
        resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


# ── App Factory ──────────────────────────────────────────────

def create_app(engine) -> web.Application:
    """Create the dashboard web application.

    Args:
        engine: RuneClawEngine instance (or any object with
                portfolio, risk, cost, state attributes).
    """
    app = web.Application(middlewares=[cors_middleware, auth_middleware])
    app["engine"] = engine
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/positions", handle_positions)
    app.router.add_get("/api/signals", handle_signals)
    # Operational probes / scrape (unauthenticated; non-sensitive).
    app.router.add_get("/health", handle_health)
    app.router.add_get("/ready", handle_ready)
    app.router.add_get("/metrics", handle_metrics)
    return app
