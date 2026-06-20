"""
RUNECLAW Market Scanner -- detects tradeable opportunities.
Fetches top movers from spot + futures markets, screens for volume
anomalies and momentum, classifies by asset category, and emits
structured MarketSignal objects for downstream analysis.

Supported universes:
  - "all_markets" (default) — spot crypto + ALL TradFi futures in one scan
  - "all"        — spot crypto only
  - "solana"     — Solana ecosystem priority
  - "stocks"     — US stock tokenized perps
  - "hybrid"     — crypto + stocks combined
  - Single-category: "metals", "commodities", "etfs", "pre_ipo", "tradfi"
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime
from bot.compat import UTC
from typing import Optional

import ccxt.async_support as ccxt

from bot.config import (
    CONFIG, SOLANA_ECOSYSTEM_SYMBOLS, US_STOCK_SYMBOLS,
    METAL_PERPETUALS, COMMODITY_PERPETUALS, PRE_IPO_PERPETUALS,
    ETF_PERPETUALS, TRADFI_PERPETUALS, STOCK_PERPETUALS,
)
from bot.utils.logger import audit, system_log
from bot.utils.models import MarketSignal


# ── Symbol → Category classification ────────────────────────────
_METAL_SET = set(METAL_PERPETUALS)
_COMMODITY_SET = set(COMMODITY_PERPETUALS)
_PRE_IPO_SET = set(PRE_IPO_PERPETUALS)
_ETF_SET = set(ETF_PERPETUALS)
_TRADFI_SET = set(TRADFI_PERPETUALS)
_STOCK_SET = set(US_STOCK_SYMBOLS)
_STOCK_PERP_SET = set(STOCK_PERPETUALS)


def has_futures_market(scanner_instance, symbol: str) -> bool:
    """Check if a symbol has a futures market on the exchange.
    
    Uses the cached set from the last scan cycle. If cache is empty
    (first scan not yet completed), returns True (optimistic).
    """
    if not scanner_instance._futures_symbols:
        return True  # no cache yet, assume available
    spot_fmt = symbol.split(":")[0] if ":" in symbol else symbol
    return spot_fmt in scanner_instance._futures_symbols


def _classify_symbol(symbol: str) -> str:
    """Return the asset category for a given symbol."""
    if symbol in _METAL_SET:
        return "Metal"
    if symbol in _COMMODITY_SET:
        return "Commodity"
    if symbol in _PRE_IPO_SET:
        return "Pre-IPO"
    if symbol in _ETF_SET:
        return "ETF"
    if symbol in _STOCK_PERP_SET or symbol in _STOCK_SET:
        return "Stock"
    return "Crypto"


# Category display config: (icon, sort_priority)
CATEGORY_META: dict[str, tuple[str, int]] = {
    "Crypto":    ("\U0001f4b0", 0),
    "Metal":     ("\u2699\ufe0f", 1),
    "Commodity": ("\U0001f6e2\ufe0f", 2),
    "ETF":       ("\U0001f4ca", 3),
    "Pre-IPO":   ("\U0001f680", 4),
    "Stock":     ("\U0001f4c8", 5),
}


class MarketScanner:
    """Scans the Bitget spot + futures markets for actionable signals."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.Exchange] = None
        self._futures_exchange: Optional[ccxt.Exchange] = None
        self._volume_history: dict[str, list[float]] = {}  # rolling window
        self._lock = threading.RLock()
        # GETCLAW: cache of symbols with valid futures markets.
        # Built from fetch_tickers(USDT-FUTURES) response each scan cycle.
        # Spot symbols not in this set are filtered out before analysis.
        self._futures_symbols: set[str] = set()
        self._futures_symbols_raw: set[str] = set()  # raw exchange format

    async def _get_exchange(self) -> ccxt.Exchange:
        """Spot exchange for crypto/stock scanning."""
        if self._exchange is None:
            self._exchange = ccxt.bitget({
                "sandbox": CONFIG.exchange.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
            })
        return self._exchange

    async def _get_futures_exchange(self) -> ccxt.Exchange:
        """Futures (swap) exchange for TradFi perpetuals scanning."""
        if self._futures_exchange is None:
            self._futures_exchange = ccxt.bitget({
                "sandbox": CONFIG.exchange.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
                "options": {
                    "defaultType": "swap",
                },
            })
        return self._futures_exchange

    # ── Main scan entry point ────────────────────────────────────

    async def scan(self) -> list[MarketSignal]:
        """
        Fetch tickers, rank by 24h change, filter for volume spikes.
        Returns the top N signals sorted by momentum, with asset_category set.
        """
        from bot.config import RUNTIME
        universe = RUNTIME.asset_universe

        # Futures-only universes
        FUTURES_UNIVERSES: dict[str, set[str]] = {
            "metals": _METAL_SET,
            "commodities": _COMMODITY_SET,
            "pre_ipo": _PRE_IPO_SET,
            "etfs": _ETF_SET,
            "tradfi": _TRADFI_SET,
        }

        if universe == "all_markets":
            # ── Unified scan: spot + futures in parallel ──
            return await self._scan_all_markets()
        elif universe in FUTURES_UNIVERSES:
            return await self._scan_futures(FUTURES_UNIVERSES[universe])
        else:
            return await self._scan_spot(universe)

    # ── Unified all-markets scan ─────────────────────────────────

    async def _scan_all_markets(self) -> list[MarketSignal]:
        """Fetch spot crypto AND futures TradFi tickers in parallel."""
        spot_task = self._fetch_spot_tickers()
        futures_task = self._fetch_futures_tickers()

        spot_result, futures_result = await asyncio.gather(
            spot_task, futures_task, return_exceptions=True,
        )

        signals: list[MarketSignal] = []
        seen_symbols: set[str] = set()

        # GETCLAW: build futures symbol cache from the futures ticker response.
        # This lets us reject spot-only tokens BEFORE wasting LLM analysis time.
        if isinstance(futures_result, dict):
            self._futures_symbols_raw = set(futures_result.keys())
            # Build spot-format lookup: "BTC/USDT:USDT" → "BTC/USDT"
            self._futures_symbols = set()
            for fs in self._futures_symbols_raw:
                # Strip :USDT suffix to match spot format
                spot_fmt = fs.split(":")[0] if ":" in fs else fs
                self._futures_symbols.add(spot_fmt)

        # Process spot tickers (crypto) — only include symbols with futures markets
        if isinstance(spot_result, dict):
            filtered_count = 0
            for symbol, tick in spot_result.items():
                if not symbol.endswith("/USDT"):
                    continue
                # GETCLAW: skip spot-only symbols (no futures = can't trade)
                if self._futures_symbols and symbol not in self._futures_symbols:
                    filtered_count += 1
                    continue
                sig = self._process_ticker(symbol, tick, min_vol=50_000)
                if sig:
                    seen_symbols.add(symbol)
                    signals.append(sig)
            if filtered_count > 0:
                audit(system_log,
                      f"Futures filter: {filtered_count} spot-only symbols skipped",
                      action="scan_filter", result="OK")
        else:
            audit(system_log, f"Spot fetch error: {spot_result}",
                  action="scan", result="PARTIAL")

        # Process futures tickers (TradFi perpetuals)
        if isinstance(futures_result, dict):
            for symbol, tick in futures_result.items():
                if symbol not in _TRADFI_SET:
                    continue
                sig = self._process_ticker(symbol, tick, min_vol=5_000)
                if sig:
                    seen_symbols.add(symbol)
                    signals.append(sig)
        else:
            audit(system_log, f"Futures fetch error: {futures_result}",
                  action="scan", result="PARTIAL")

        if not signals:
            audit(system_log, "All-markets scan: no signals",
                  action="scan", result="EMPTY")
            return []

        # Sort by absolute momentum, then allocate slots per category
        signals.sort(key=lambda s: abs(s.momentum_score), reverse=True)
        top = self._allocate_slots(signals)

        self._evict_stale(seen_symbols)

        cats = {}
        for s in top:
            cats[s.asset_category] = cats.get(s.asset_category, 0) + 1
        audit(system_log,
              f"All-markets scan: {len(top)} signals from {len(signals)} pairs",
              action="scan", result="OK",
              data={"count": len(top), "categories": cats})
        return top

    def _allocate_slots(self, signals: list[MarketSignal]) -> list[MarketSignal]:
        """
        Smart slot allocation: ensure each category gets representation,
        then fill remaining slots with top movers overall.

        Allocation:
          - Each category with signals gets at least 2 slots (or all if fewer)
          - Remaining slots filled by strongest movers across all categories
        """
        max_total = CONFIG.top_movers_count

        # Group by category
        by_cat: dict[str, list[MarketSignal]] = {}
        for s in signals:
            by_cat.setdefault(s.asset_category, []).append(s)

        # Guarantee slots per category
        guaranteed: list[MarketSignal] = []
        used: set[str] = set()  # symbol dedup
        min_per_cat = 2

        for cat in sorted(by_cat, key=lambda c: CATEGORY_META.get(c, ("", 99))[1]):
            cat_signals = by_cat[cat]
            for s in cat_signals[:min_per_cat]:
                if s.symbol not in used:
                    guaranteed.append(s)
                    used.add(s.symbol)

        # Fill remaining from overall top movers
        remaining = max_total - len(guaranteed)
        if remaining > 0:
            for s in signals:
                if s.symbol not in used:
                    guaranteed.append(s)
                    used.add(s.symbol)
                    if len(guaranteed) >= max_total:
                        break

        return guaranteed[:max_total]

    # ── Spot-only scan ───────────────────────────────────────────

    async def _scan_spot(self, universe: str) -> list[MarketSignal]:
        """Original spot-only scan logic."""
        try:
            tickers = await self._fetch_spot_tickers()
        except Exception as exc:
            audit(system_log, f"Scanner exchange error: {exc}",
                  action="scan", result="ERROR")
            return []

        signals: list[MarketSignal] = []
        seen_symbols: set[str] = set()

        for symbol, tick in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            sig = self._process_ticker(symbol, tick, min_vol=50_000)
            if sig:
                seen_symbols.add(symbol)
                signals.append(sig)

        signals.sort(key=lambda s: abs(s.momentum_score), reverse=True)

        # Universe-specific filtering
        if universe == "solana":
            solana_set = set(SOLANA_ECOSYSTEM_SYMBOLS)
            sol = [s for s in signals if s.symbol in solana_set]
            other = [s for s in signals if s.symbol not in solana_set]
            top = (sol + other)[:CONFIG.top_movers_count]
        elif universe == "stocks":
            top = [s for s in signals if s.symbol in _STOCK_SET][:CONFIG.top_movers_count]
        elif universe == "hybrid":
            stock_sigs = [s for s in signals if s.symbol in _STOCK_SET]
            crypto_sigs = [s for s in signals if s.symbol not in _STOCK_SET]
            half = max(CONFIG.top_movers_count // 2, 5)
            top = (stock_sigs[:half] + crypto_sigs[:half])[:CONFIG.top_movers_count]
        else:
            top = signals[:CONFIG.top_movers_count]

        self._evict_stale(seen_symbols)
        audit(system_log, f"Scan complete: {len(top)} signals from {len(signals)} pairs",
              action="scan", result="OK", data={"count": len(top)})
        return top

    # ── Futures-only scan ────────────────────────────────────────

    async def _scan_futures(self, symbol_filter: set[str]) -> list[MarketSignal]:
        """Scan only futures perpetuals matching the given symbol set."""
        try:
            tickers = await self._fetch_futures_tickers()
        except Exception as exc:
            audit(system_log, f"Scanner futures error: {exc}",
                  action="scan", result="ERROR")
            return []

        signals: list[MarketSignal] = []
        seen_symbols: set[str] = set()

        for symbol, tick in tickers.items():
            if symbol not in symbol_filter:
                continue
            sig = self._process_ticker(symbol, tick, min_vol=5_000)
            if sig:
                seen_symbols.add(symbol)
                signals.append(sig)

        signals.sort(key=lambda s: abs(s.momentum_score), reverse=True)
        top = signals[:CONFIG.top_movers_count]

        self._evict_stale(seen_symbols)
        audit(system_log, f"Futures scan: {len(top)} signals from {len(signals)} pairs",
              action="scan", result="OK", data={"count": len(top)})
        return top

    # ── Ticker fetchers ──────────────────────────────────────────

    async def _fetch_spot_tickers(self) -> dict:
        exchange = await self._get_exchange()
        return await exchange.fetch_tickers()

    async def _fetch_futures_tickers(self) -> dict:
        exchange = await self._get_futures_exchange()
        return await exchange.fetch_tickers(params={
            "productType": "USDT-FUTURES",
        })

    # ── Shared processing ────────────────────────────────────────

    def _process_ticker(
        self, symbol: str, tick: dict, min_vol: float = 50_000,
    ) -> Optional[MarketSignal]:
        """Convert a raw ticker dict into a MarketSignal, or None if filtered."""
        # Skip delisted or suspended tickers
        if tick.get("active") is False:
            return None
        tick_status = tick.get("info", {}).get("status", "").lower()
        if tick_status in ("delisted", "suspended"):
            return None

        try:
            change = float(tick.get("percentage", 0) or 0)
            volume = float(tick.get("quoteVolume", 0) or 0)
            price = float(tick.get("last", 0) or 0)
        except (TypeError, ValueError):
            return None

        if price <= 0 or volume < min_vol:
            return None

        spike = self._detect_volume_spike(symbol, volume)
        momentum = self._momentum_score(change, spike)
        category = _classify_symbol(symbol)

        return MarketSignal(
            symbol=symbol,
            price=price,
            change_pct_24h=round(change, 2),
            volume_usd_24h=round(volume, 2),
            volume_spike=spike,
            momentum_score=round(momentum, 3),
            timestamp=datetime.now(UTC),
            asset_category=category,
        )

    # ── Internal helpers ─────────────────────────────────────────

    def _detect_volume_spike(self, symbol: str, current_vol: float) -> bool:
        """True if current volume is >2x the rolling average."""
        # Note: rapid rescans may dampen spike detection as recent high volumes
        # are included in the baseline. This is acceptable for the 5-min scan interval.
        with self._lock:
            history = self._volume_history.setdefault(symbol, [])
            if len(history) >= 3:
                avg = sum(history) / len(history)
                is_spike = current_vol > avg * 2.0
            else:
                is_spike = False
            history.append(current_vol)
            if len(history) > 20:
                self._volume_history[symbol] = history[-20:]
            return is_spike

    @staticmethod
    def _momentum_score(change_pct: float, volume_spike: bool) -> float:
        """
        Simple momentum heuristic in [-1, 1].
        Positive = bullish, negative = bearish.
        """
        base = max(min(change_pct / 10.0, 1.0), -1.0)
        if volume_spike:
            base *= 1.3
        return max(min(base, 1.0), -1.0)

    def _evict_stale(self, seen_symbols: set[str]) -> None:
        """Remove volume history for symbols not seen in this scan."""
        with self._lock:
            stale = [s for s in self._volume_history if s not in seen_symbols]
            for s in stale:
                del self._volume_history[s]
            if len(self._volume_history) > 500:
                excess = len(self._volume_history) - 500
                for key in list(self._volume_history)[:excess]:
                    del self._volume_history[key]

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
        if self._futures_exchange:
            await self._futures_exchange.close()
            self._futures_exchange = None
