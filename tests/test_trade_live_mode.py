"""AUDIT-FIX-3 (bot side): the /trade/live_mode gateway endpoint.

The web 2FA step-up on a live confirm must key off the bot's AUTHORITATIVE live
capability, not a stale web-side user_controls mirror. This endpoint exposes
exactly the _trade_mode decision the propose/confirm path uses — covering BOTH
linked Telegram users (allowlist + /live) and web-only ids (fail-closed
web-live gate) — so the web layer can gate step-up correctly.

The behaviour of _trade_mode is exercised throughout the web-live authz suite
and end-to-end by the Node gateway_routes test; here we lock that the read
endpoint exists, is auth-guarded like every per-user endpoint, delegates to
_trade_mode, and is registered on the gateway router.
"""

import inspect
from pathlib import Path

from bot.web import user_gateway as ug


def test_handler_exists_and_is_guarded_and_delegates():
    assert hasattr(ug, "handle_trade_live_mode")
    src = inspect.getsource(ug.handle_trade_live_mode)
    assert "_guard_user(" in src, "must run the per-user auth guard"
    assert "_trade_mode(" in src, "must use the same authoritative decision as confirm"
    assert "live_allowed" in src


def test_route_is_registered_as_get():
    src = (Path(__file__).resolve().parent.parent
           / "bot/web/user_gateway.py").read_text(encoding="utf-8")
    assert 'add_get("/trade/live_mode", handle_trade_live_mode)' in src


def test_read_only_no_execution_side_effects():
    # A capability read must never place/confirm a trade.
    src = inspect.getsource(ug.handle_trade_live_mode)
    assert "confirm_trade" not in src
    assert "register_manual_idea" not in src
