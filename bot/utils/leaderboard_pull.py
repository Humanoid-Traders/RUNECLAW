"""
Pull the leaderboard opt-in DESIRED STATE from the website.

Users opt in to the public verifiable leaderboard by setting an anonymous
handle on the web (users.leaderboard_handle); opt-out clears it. This fetches
the full current opt-in set over the shared-secret sync channel so the engine
can publish each opted-in user's own sealed statement under their handle and
reconcile-remove anyone who dropped out. Desired-state, not a queue: no ack
round-trip, and a lost pull is healed by the next one.

Handles are NOT secrets (they are already public on the board), so unlike the
credential pull there is no decryption step and only the sync secret is
required — same trust shape as control_pull.
"""

from __future__ import annotations

import logging

from bot.utils.credential_pull import _request, SYNC_SECRET  # reuse the channel

log = logging.getLogger(__name__)


def fetch_leaderboard_optins() -> list[dict] | None:
    """The current opt-in set: [{user_id, telegram_id, handle}, ...].

    Returns ``None`` when the channel is unconfigured or the request FAILED —
    callers must then leave the board untouched (a transport blip must never
    read as a mass opt-out). Returns ``[]`` only when the website positively
    reported an empty set, which IS a real everyone-opted-out state and should
    reconcile-remove the handles this bot previously published.
    """
    if not SYNC_SECRET:
        return None
    resp = _request("/api/bot/sync/leaderboard/pending")
    if not isinstance(resp, dict) or "optins" not in resp:
        return None
    rows = resp.get("optins") or []
    return [r for r in rows if isinstance(r, dict)]
