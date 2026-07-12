"""
Cross-venue live derivatives data (Round B of the multi-venue build-out).

RUNECLAW is connected to four venues but analyzed with one venue's eyes:
funding rate — the single strongest crowd-positioning signal perps have —
was only ever read from Bitget. Venues disagree: when Bitget longs pay
0.01% while Bybit longs pay 0.10%, the crowd is concentrated somewhere
specific, and a single-venue bot cannot see it.

This module provides keyless (public-endpoint) bulk funding-rate maps
from Bybit and Hyperliquid:

  - ONE fetch_funding_rates() call per venue returns the ENTIRE funding
    map — per-symbol lookups afterwards are free, so enriching a
    200-symbol scan costs 2 HTTP calls per cache window, not 400.
  - TTL-cached (default 10 min; funding updates hourly at the fastest)
    with an in-flight lock so a concurrent scan can't stampede a venue.
  - Fail-open everywhere: a venue being down or geo-blocked degrades to
    "fewer venues in the map" — it can never delay or break the scan.

Consumers: order_flow.analyze() attaches the map + spread to
OrderFlowSignal (observability first — no vote change until the data
earns it), and the /funding Telegram command renders the comparison.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Venues polled for cross-venue funding. Bitget is not here — its rate
# arrives through the existing per-symbol order-flow fetch and is merged
# by the consumer, keeping this provider purely additive.
_VENUE_IDS = ("bybit", "hyperliquid")


def base_of(symbol: str) -> str:
    """'BTC/USDT:USDT' | 'BTC/USDT' | 'BTC' -> 'BTC'."""
    return symbol.split("/")[0].split(":")[0].upper()


class CrossVenueFunding:
    """Bulk funding-rate maps per venue, cached and fail-open."""

    def __init__(self, ttl_seconds: float = 600.0) -> None:
        self.ttl = ttl_seconds
        self._maps: dict[str, dict[str, float]] = {}      # venue -> base -> rate
        # venue -> monotonic time of last fetch attempt. Absent = never
        # fetched. NEVER use 0.0 as the sentinel here: time.monotonic()
        # counts from boot, so on a freshly restarted host (CI runner, a
        # rebooted trading VPS) now-0.0 < ttl and the empty map would look
        # "fresh" until uptime exceeds the TTL.
        self._fetched_at: dict[str, Optional[float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._exchanges: dict[str, object] = {}

    # ── venue clients (public, keyless) ───────────────────────────
    async def _exchange(self, venue_id: str):
        ex = self._exchanges.get(venue_id)
        if ex is None:
            import ccxt.async_support as ccxt
            ex = getattr(ccxt, venue_id)({
                "aiohttp_trust_env": True,
                "timeout": 15000,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
            self._exchanges[venue_id] = ex
        return ex

    async def close(self) -> None:
        for ex in self._exchanges.values():
            try:
                await ex.close()  # type: ignore[attr-defined]
            except Exception:
                pass
        self._exchanges.clear()

    # ── funding maps ──────────────────────────────────────────────
    @staticmethod
    def _parse_rates(raw: dict) -> dict[str, float]:
        """ccxt fetch_funding_rates() payload -> {base: rate}. Keeps the
        largest-magnitude rate if a base appears twice (multiple quotes)."""
        out: dict[str, float] = {}
        for sym, entry in (raw or {}).items():
            try:
                rate = entry.get("fundingRate") if isinstance(entry, dict) else None
                if rate is None:
                    continue
                b = base_of(sym)
                r = float(rate)
                if b not in out or abs(r) > abs(out[b]):
                    out[b] = r
            except Exception:
                continue
        return out

    def _is_fresh(self, venue_id: str) -> bool:
        last = self._fetched_at.get(venue_id)
        return last is not None and (time.monotonic() - last) < self.ttl

    async def _venue_map(self, venue_id: str) -> dict[str, float]:
        if self._is_fresh(venue_id):
            return self._maps.get(venue_id, {})
        lock = self._locks.setdefault(venue_id, asyncio.Lock())
        async with lock:
            # Re-check under the lock — a concurrent caller may have filled it.
            if self._is_fresh(venue_id):
                return self._maps.get(venue_id, {})
            try:
                ex = await self._exchange(venue_id)
                raw = await ex.fetch_funding_rates()  # type: ignore[attr-defined]
                self._maps[venue_id] = self._parse_rates(raw)
                self._fetched_at[venue_id] = time.monotonic()
                logger.debug("cross-venue funding refreshed: %s (%d bases)",
                             venue_id, len(self._maps[venue_id]))
            except Exception as exc:
                # Fail-open: keep the stale map (if any) and back off a
                # quarter-TTL so a down venue isn't hammered every call.
                self._fetched_at[venue_id] = time.monotonic() - self.ttl * 0.75
                logger.debug("cross-venue funding fetch failed for %s: %s",
                             venue_id, str(exc)[:120])
            return self._maps.get(venue_id, {})

    # ── public API ────────────────────────────────────────────────
    async def rates_for(self, symbol: str) -> dict[str, float]:
        """{venue_id: funding_rate} for a symbol's base across the polled
        venues. Missing venues simply absent. Never raises."""
        b = base_of(symbol)
        out: dict[str, float] = {}
        try:
            maps = await asyncio.gather(
                *(self._venue_map(v) for v in _VENUE_IDS))
        except Exception:
            return out
        for venue_id, m in zip(_VENUE_IDS, maps):
            if b in m:
                out[venue_id] = m[b]
        return out

    @staticmethod
    def divergence(rates: dict[str, float],
                   home_rate: Optional[float] = None) -> Optional[dict]:
        """Spread stats across venues (optionally including the home
        venue's rate). None when fewer than 2 venues report."""
        vals = list(rates.values())
        if home_rate is not None:
            vals.append(float(home_rate))
        if len(vals) < 2:
            return None
        return {
            "spread": max(vals) - min(vals),
            "mean": sum(vals) / len(vals),
            "venues": len(vals),
        }


# Shared singleton — one cache for the whole process.
CROSS_VENUE = CrossVenueFunding()
