"""Operator-facing readiness + enablement for web live trading.

The web live gate (``web_live_gate.evaluate``) is pure — it decides from five
booleans. This module SOURCES those booleans for a given user from the live
stores (credential store, per-user Authority Envelope store, user store) and
the operator feature switch, so an operator can see exactly what stands between
a web user and live trading, and flip the one per-user control they own.

Nothing here places a trade or changes the global switch (that stays a
deployment-level env var, ``WEB_LIVE_TRADING_ENABLED``). It reads state and,
for ``set_user_enabled``, toggles the dedicated per-user ``web_live_enabled``
opt-in — the same flag the gate reads.
"""

from __future__ import annotations

from typing import Any

from bot.config import CONFIG
from bot.web import web_live_gate


def _has_own_keys(tg_id: str) -> bool:
    try:
        from bot.core.exchange_credentials import get_credential_store
        return bool(get_credential_store().has(tg_id))
    except Exception:
        return False


def _envelope_enforcing(tg_id: str) -> bool:
    try:
        from bot.guardian.user_authority_store import get_user_authority_store
        return bool(get_user_authority_store().is_enforcing(tg_id))
    except Exception:
        return False


def user_readiness(users: Any, tg_id: str) -> dict:
    """Source the five gate inputs for ``tg_id`` and evaluate. Returns
    ``{allowed, reason, checklist}`` — the operator's readiness view."""
    opt_in = False
    fn = getattr(users, "web_live_enabled", None)
    if callable(fn):
        try:
            opt_in = bool(fn(tg_id))
        except Exception:
            opt_in = False
    dec = web_live_gate.evaluate(
        feature_enabled=web_live_gate.feature_enabled(),
        bot_is_live=CONFIG.is_live(),
        user_opted_in=opt_in,
        has_own_keys=_has_own_keys(tg_id),
        envelope_enforcing=_envelope_enforcing(tg_id),
    )
    return {"allowed": dec.allowed, "reason": dec.reason, "checklist": dec.checklist}


def set_user_enabled(users: Any, tg_id: str, enabled: bool) -> bool:
    """Flip the per-user web_live_enabled opt-in (web:<id> only). Returns success."""
    fn = getattr(users, "set_web_live_enabled", None)
    if not callable(fn):
        return False
    try:
        return bool(fn(tg_id, enabled))
    except Exception:
        return False


_CHECK_LABELS = {
    "feature_enabled": "Operator feature switch (WEB_LIVE_TRADING_ENABLED)",
    "bot_is_live": "Bot in live mode",
    "user_opted_in": "User's web_live_enabled opt-in",
    "has_own_keys": "User's own exchange keys connected",
    "envelope_enforcing": "Authority Envelope bound in enforce mode",
}


def human_readable(tg_id: str, readiness: dict) -> str:
    """Plain-text operator readiness card (no markup)."""
    cl = readiness.get("checklist", {})
    head = ("✅ READY for live web trading" if readiness.get("allowed")
            else "⛔ NOT ready — " + str(readiness.get("reason", "")))
    lines = [f"Web-live readiness for {tg_id}", head, ""]
    for key, label in _CHECK_LABELS.items():
        lines.append(f"  {'✅' if cl.get(key) else '⬜'} {label}")
    return "\n".join(lines)
