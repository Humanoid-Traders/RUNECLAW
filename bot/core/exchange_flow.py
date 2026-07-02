"""
RUNECLAW Real-Time Exchange Flow Provider
==========================================
Feeds live funding rates and open interest from Bitget (via ccxt) into
the RUNECLAW analysis pipeline.

Consumers:
  - smart_money.SmartMoneyEngine  (analyze_exchange_flow, composite_flow_signal)
  - macro_events.MacroEventProvider(funding_provider=provider.funding_rate_provider)
  - Telegram alert formatting

Design rules (consistent with the rest of RUNECLAW):
  - Fail-open: every fetch is independently guarded; errors degrade to
    None / 0.0 and log a warning.  Never raises.
  - Thread-safe: RLock protects shared cache.
  - Bounded memory: cache is keyed per symbol, pruned on access.
  - No extra dependencies beyond ccxt + stdlib.
"""

from __future__ import annotations

import asyncio
import logging
import inspect
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger("runeclaw.exchange_flow")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_swap_symbol(symbol: str) -> str:
    """Convert a spot symbol to Bitget swap format if needed.

    'BTC/USDT'       -> 'BTC/USDT:USDT'
    'BTC/USDT:USDT'  -> 'BTC/USDT:USDT'  (no-op)
    """
    if ":" not in symbol:
        # Derive settle currency from quote (e.g. BTC/USDT -> USDT)
        quote = symbol.split("/")[-1] if "/" in symbol else "USDT"
        return f"{symbol}:{quote}"
    return symbol


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------

_EMPTY_CACHE: dict = {
    "funding_rate": None,
    "oi_usd": None,
    "oi_prev_usd": None,
    "updated_at": 0.0,
    "oi_updated_at": 0.0,
    "funding_history": [],
}


def _new_entry() -> dict:
    return dict(_EMPTY_CACHE, funding_history=[])


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class ExchangeFlowProvider:
    """Real-time funding rate + open interest provider backed by ccxt.bitget.

    Parameters
    ----------
    exchange_factory : callable, optional
        A callable that returns a ``ccxt.async_support.bitget`` instance
        (or any ccxt async exchange supporting ``fetch_funding_rate``,
        ``fetch_open_interest``, ``fetch_funding_rate_history``).
        If *None*, all fetch methods return None gracefully.
    funding_ttl : float
        Seconds before a cached funding rate is considered stale.  Default 60.
    oi_ttl : float
        Seconds before cached open-interest data is considered stale.  Default 300.
    max_symbols : int
        Maximum number of symbols to keep in cache (LRU-ish pruning).
    """

    def __init__(
        self,
        exchange_factory: Optional[Callable[[], Any]] = None,
        funding_ttl: float = 60.0,
        oi_ttl: float = 300.0,
        max_symbols: int = 200,
    ) -> None:
        self._exchange_factory = exchange_factory
        self._funding_ttl = funding_ttl
        self._oi_ttl = oi_ttl
        self._max_symbols = max_symbols

        self._lock = threading.RLock()
        self._cache: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_exchange(self) -> Any:
        """Obtain an exchange instance.  Returns None on failure.

        Supports sync AND async factories: the engine wires
        MarketScanner._get_exchange, which is a coroutine function — the old
        synchronous call returned a bare coroutine object, every subsequent
        fetch raised AttributeError into the broad except, and the provider
        never returned live funding/OI data at all.
        """
        if self._exchange_factory is None:
            return None
        try:
            result = self._exchange_factory()
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("exchange_factory raised: %s", exc)
            return None

    def _entry(self, symbol: str) -> dict:
        """Return cache entry for *symbol*, creating if absent."""
        with self._lock:
            if symbol not in self._cache:
                self._cache[symbol] = _new_entry()
            return self._cache[symbol]

    def _prune(self) -> None:
        """Drop oldest symbols if cache exceeds max_symbols."""
        with self._lock:
            if len(self._cache) <= self._max_symbols:
                return
            # Sort by updated_at ascending, drop the oldest
            items = sorted(
                self._cache.items(),
                key=lambda kv: max(kv[1].get("updated_at", 0), kv[1].get("oi_updated_at", 0)),
            )
            excess = len(self._cache) - self._max_symbols
            for key, _ in items[:excess]:
                del self._cache[key]

    # ------------------------------------------------------------------
    # Async public API
    # ------------------------------------------------------------------

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Return the current funding rate as a fraction per 8 h.

        Example: 0.001 means 0.10%.  Returns None on error or if the
        exchange is unavailable.
        """
        swap = _to_swap_symbol(symbol)
        entry = self._entry(swap)

        # Return cached value if fresh
        if entry["funding_rate"] is not None and (time.time() - entry["updated_at"]) < self._funding_ttl:
            return entry["funding_rate"]

        exchange = await self._get_exchange()
        if exchange is None:
            return entry.get("funding_rate")

        try:
            result = await exchange.fetch_funding_rate(swap)
            rate = result.get("fundingRate")
            if rate is not None:
                rate = float(rate)
                with self._lock:
                    entry["funding_rate"] = rate
                    entry["updated_at"] = time.time()
                self._prune()
                return rate
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_funding_rate(%s) failed: %s", swap, exc)

        return entry.get("funding_rate")

    async def get_open_interest(self, symbol: str) -> Optional[dict]:
        """Return open interest data.

        Returns
        -------
        dict or None
            ``{"oi_usd": float, "oi_change_pct": float}``
            ``oi_change_pct`` is the percent change vs the previous observation.
            Returns None if the exchange is unavailable or the fetch fails.
        """
        swap = _to_swap_symbol(symbol)
        entry = self._entry(swap)

        # Return cached value if fresh
        if entry["oi_usd"] is not None and (time.time() - entry["oi_updated_at"]) < self._oi_ttl:
            change_pct = 0.0
            if entry["oi_prev_usd"] and entry["oi_prev_usd"] > 0:
                change_pct = (entry["oi_usd"] - entry["oi_prev_usd"]) / entry["oi_prev_usd"] * 100
            return {"oi_usd": entry["oi_usd"], "oi_change_pct": round(change_pct, 3)}

        exchange = await self._get_exchange()
        if exchange is None:
            if entry["oi_usd"] is not None:
                return {"oi_usd": entry["oi_usd"], "oi_change_pct": 0.0}
            return None

        try:
            oi = await exchange.fetch_open_interest(swap)
            oi_usd = oi.get("openInterestValue")
            if oi_usd is None:
                amt = oi.get("openInterestAmount")
                if amt is not None:
                    # Cannot compute USD value without a price; store raw amount
                    oi_usd = float(amt)
            if oi_usd is not None:
                oi_usd = float(oi_usd)
                with self._lock:
                    entry["oi_prev_usd"] = entry["oi_usd"]
                    entry["oi_usd"] = oi_usd
                    entry["oi_updated_at"] = time.time()
                self._prune()
                change_pct = 0.0
                if entry["oi_prev_usd"] and entry["oi_prev_usd"] > 0:
                    change_pct = (oi_usd - entry["oi_prev_usd"]) / entry["oi_prev_usd"] * 100
                return {"oi_usd": oi_usd, "oi_change_pct": round(change_pct, 3)}
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_open_interest(%s) failed: %s", swap, exc)

        if entry["oi_usd"] is not None:
            return {"oi_usd": entry["oi_usd"], "oi_change_pct": 0.0}
        return None

    async def get_funding_history(self, symbol: str, limit: int = 20) -> list[dict]:
        """Return recent funding rate snapshots.

        Each entry is a dict with keys: ``timestamp``, ``funding_rate``,
        ``datetime`` (ISO-8601 string).

        Returns an empty list on error.
        """
        swap = _to_swap_symbol(symbol)
        entry = self._entry(swap)

        # If we have a cached history that is reasonably recent, return it
        if entry["funding_history"] and (time.time() - entry["updated_at"]) < self._funding_ttl:
            return entry["funding_history"][-limit:]

        exchange = await self._get_exchange()
        if exchange is None:
            return entry.get("funding_history", [])[-limit:]

        try:
            raw = await exchange.fetch_funding_rate_history(swap, limit=limit)
            history: list[dict] = []
            for item in (raw or []):
                history.append({
                    "timestamp": item.get("timestamp"),
                    "funding_rate": float(item["fundingRate"]) if item.get("fundingRate") is not None else None,
                    "datetime": item.get("datetime", ""),
                })
            with self._lock:
                entry["funding_history"] = history
            return history[-limit:]
        except Exception as exc:  # noqa: BLE001
            logger.warning("fetch_funding_rate_history(%s) failed: %s", swap, exc)

        return entry.get("funding_history", [])[-limit:]

    async def get_flow_summary(self, symbol: str) -> dict:
        """Combined view of funding rate, OI, funding trend, and squeeze risk.

        Returns
        -------
        dict with keys:
            funding_rate, oi_usd, oi_change_pct, funding_trend,
            squeeze_risk, interpretation.
        """
        # Fetch all data concurrently
        fr_task = asyncio.ensure_future(self.get_funding_rate(symbol))
        oi_task = asyncio.ensure_future(self.get_open_interest(symbol))
        hist_task = asyncio.ensure_future(self.get_funding_history(symbol, limit=20))

        funding_rate = await fr_task
        oi_data = await oi_task
        history = await hist_task

        oi_usd = oi_data["oi_usd"] if oi_data else None
        oi_change_pct = oi_data["oi_change_pct"] if oi_data else None

        # Determine funding trend from history
        funding_trend = self._compute_funding_trend(history)

        # Determine squeeze risk
        squeeze_risk, interpretation = self._assess_squeeze_risk(
            funding_rate, oi_change_pct, funding_trend,
        )

        return {
            "funding_rate": funding_rate,
            "oi_usd": oi_usd,
            "oi_change_pct": oi_change_pct,
            "funding_trend": funding_trend,
            "squeeze_risk": squeeze_risk,
            "interpretation": interpretation,
        }

    # ------------------------------------------------------------------
    # Synchronous callable for MacroEventProvider
    # ------------------------------------------------------------------

    def funding_rate_provider(self, symbol: str) -> float:
        """Synchronous accessor returning the last cached funding rate.

        Designed as the ``funding_provider`` callable for
        ``MacroEventProvider(funding_provider=provider.funding_rate_provider)``.

        Returns 0.0 if no cached data is available.
        """
        swap = _to_swap_symbol(symbol)
        with self._lock:
            entry = self._cache.get(swap)
        if entry is None:
            return 0.0
        rate = entry.get("funding_rate")
        return float(rate) if rate is not None else 0.0

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    async def format_for_telegram(self, symbol: str) -> str:
        """Return an HTML-formatted summary suitable for Telegram messages."""
        summary = await self.get_flow_summary(symbol)

        fr = summary.get("funding_rate")
        oi = summary.get("oi_usd")
        oi_chg = summary.get("oi_change_pct")
        trend = summary.get("funding_trend", "UNKNOWN")
        risk = summary.get("squeeze_risk", "NONE")
        interp = summary.get("interpretation", "")

        lines: list[str] = [
            f"<b>Exchange Flow: {symbol}</b>",
            "",
        ]

        # Funding rate
        if fr is not None:
            fr_pct = fr * 100
            direction = "+" if fr >= 0 else ""
            lines.append(f"Funding Rate: <code>{direction}{fr_pct:.4f}%</code> /8h")
        else:
            lines.append("Funding Rate: <i>unavailable</i>")

        # Open interest
        if oi is not None:
            if oi >= 1_000_000_000:
                oi_str = f"${oi / 1_000_000_000:.2f}B"
            elif oi >= 1_000_000:
                oi_str = f"${oi / 1_000_000:.2f}M"
            else:
                oi_str = f"${oi:,.0f}"
            chg_str = f" ({oi_chg:+.1f}%)" if oi_chg is not None else ""
            lines.append(f"Open Interest: <code>{oi_str}</code>{chg_str}")
        else:
            lines.append("Open Interest: <i>unavailable</i>")

        lines.append(f"Funding Trend: <b>{trend}</b>")
        lines.append(f"Squeeze Risk: <b>{risk}</b>")

        if interp:
            lines.append("")
            lines.append(f"<i>{interp}</i>")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Analysis helpers (pure functions)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_funding_trend(history: list[dict]) -> str:
        """Classify the recent funding trend as RISING / FALLING / STABLE."""
        rates = [
            h["funding_rate"] for h in (history or [])
            if h.get("funding_rate") is not None
        ]
        if len(rates) < 4:
            return "STABLE"

        recent = rates[-4:]
        older = rates[:-4] if len(rates) > 4 else rates[:2]

        recent_avg = sum(recent) / len(recent)
        older_avg = sum(older) / len(older)

        diff = recent_avg - older_avg
        threshold = 0.00005  # 0.005% absolute shift is meaningful

        if diff > threshold:
            return "RISING"
        elif diff < -threshold:
            return "FALLING"
        return "STABLE"

    @staticmethod
    def _assess_squeeze_risk(
        funding_rate: Optional[float],
        oi_change_pct: Optional[float],
        funding_trend: str,
    ) -> tuple[str, str]:
        """Assess squeeze risk from funding + OI dynamics.

        Returns (risk_level, interpretation).
        """
        if funding_rate is None:
            return "NONE", "Insufficient data for squeeze assessment"

        abs_fr = abs(funding_rate)

        # Thresholds (fraction per 8h)
        extreme = 0.001     # 0.10%
        elevated = 0.0005   # 0.05%

        crowd_long = funding_rate > 0
        crowd_label = "longs" if crowd_long else "shorts"
        squeeze_dir = "long squeeze" if crowd_long else "short squeeze"

        # HIGH: extreme funding + rising OI + accelerating trend
        if abs_fr >= extreme:
            oi_rising = (oi_change_pct is not None and oi_change_pct > 5)
            trend_accel = (
                (crowd_long and funding_trend == "RISING")
                or (not crowd_long and funding_trend == "FALLING")
            )
            if oi_rising or trend_accel:
                return "HIGH", (
                    f"Extreme funding ({funding_rate:+.4f}) with "
                    f"{'rising OI' if oi_rising else 'accelerating trend'} -- "
                    f"crowded {crowd_label}, {squeeze_dir} likely"
                )
            return "MEDIUM", (
                f"Extreme funding ({funding_rate:+.4f}) indicates crowded "
                f"{crowd_label} -- {squeeze_dir} possible"
            )

        # MEDIUM: elevated funding
        if abs_fr >= elevated:
            return "MEDIUM", (
                f"Elevated funding ({funding_rate:+.4f}) -- "
                f"{crowd_label} moderately crowded"
            )

        # LOW: mild but non-trivial
        if abs_fr >= elevated * 0.5:
            return "LOW", (
                f"Mild funding bias ({funding_rate:+.4f}) -- "
                f"slight {crowd_label} lean"
            )

        return "NONE", "Funding is neutral -- no squeeze setup detected"
