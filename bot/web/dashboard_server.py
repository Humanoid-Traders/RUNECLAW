"""
RUNECLAW — Live Web Dashboard API Server
=========================================
Thin aiohttp layer exposing engine state as JSON endpoints.
Runs alongside the Telegram bot on the same asyncio loop.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

# Lazy imports — engine may not be available during module load
_ENGINE = None


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

    # Portfolio
    try:
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
        data["engine"] = {
            "state": state_name,
            "scan_interval": getattr(engine, "_scan_interval", 60),
            "pending_ideas": len(getattr(engine, "pending_ideas", [])),
            "simulation_mode": True,
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
        trailing = engine.portfolio.get_trailing_status()
        for pos in engine.portfolio.open_positions:
            d = _safe_dict(pos)
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
        for t in (engine.portfolio.trade_history or [])[-20:]:
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


# ── CORS Middleware ──────────────────────────────────────────

@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# ── App Factory ──────────────────────────────────────────────

def create_app(engine) -> web.Application:
    """Create the dashboard web application.

    Args:
        engine: RuneClawEngine instance (or any object with
                portfolio, risk, cost, state attributes).
    """
    app = web.Application(middlewares=[cors_middleware])
    app["engine"] = engine
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/state", handle_state)
    app.router.add_get("/api/positions", handle_positions)
    app.router.add_get("/api/signals", handle_signals)
    return app
