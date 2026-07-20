"""
Pull Node-side intelligence surfaces for Telegram parity (/exposure /research
/rwa).

Cross-venue exposure netting, research dossiers, and the RWA radar are
implemented in the web app (app/lib/exposure.js, research.js, rwa.js) — one
brain, one implementation. These fetch the SAME payloads the web panels render
over the shared-secret sync channel, so the Telegram commands never fork the
logic. All read-only; ``None`` = channel unconfigured or failed (commands say
"link the web app" instead of failing loudly).
"""

from __future__ import annotations

import re
import urllib.parse

from bot.utils.credential_pull import _request, SYNC_SECRET  # reuse the channel

_SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}$")


def fetch_exposure(telegram_id: str) -> dict | None:
    """The caller's own cross-venue exposure (perp vs on-chain netting)."""
    if not SYNC_SECRET or not telegram_id:
        return None
    tg = urllib.parse.quote(str(telegram_id)[:32])
    return _request(f"/api/bot/sync/exposure?telegram_id={tg}")


def fetch_research(symbol: str) -> dict | None:
    """A research dossier for a base symbol (venue data + recorded history)."""
    if not SYNC_SECRET:
        return None
    base = str(symbol or "").upper().strip()
    base = re.sub(r"[^A-Z0-9]", "", base).removesuffix("USDT")[:10]
    if not _SYMBOL_RE.match(base):
        return None
    return _request(f"/api/bot/sync/research/{base}")


def fetch_rwa() -> dict | None:
    """The tokenized-RWA sector radar (live venue tickers, read-only)."""
    if not SYNC_SECRET:
        return None
    return _request("/api/bot/sync/rwa")


def fetch_onchain_flow() -> dict | None:
    """The DEX taker-flow radar (keyless, 24h buy/sell balance per major).

    Feeds the engine's gated on-chain voter — the SAME payload the public
    Markets panel renders. None = channel unconfigured or failed (the voter
    simply contributes nothing; analysis never blocks on this)."""
    if not SYNC_SECRET:
        return None
    return _request("/api/bot/sync/onchain-flow")
