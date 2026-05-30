"""
RUNECLAW Market Scanner -- detects tradeable opportunities.
Fetches top movers, screens for volume anomalies and momentum,
and emits structured MarketSignal objects for downstream analysis.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Optional

import ccxt.async_support as ccxt

from bot.config import CONFIG
from bot.utils.logger import audit, system_log
from bot.utils.models import MarketSignal


class MarketScanner:
    """Scans the Bitget spot market for actionable signals."""

    def __init__(self) -> None:
        self._exchange: Optional[ccxt.Exchange] = None
        self._volume_history: dict[str, list[float]] = {}  # rolling window
        self._lock = threading.RLock()

    async def _get_exchange(self) -> ccxt.Exchange:
        if self._exchange is None:
            self._exchange = ccxt.bitget({
                "apiKey": CONFIG.exchange.api_key,
                "secret": CONFIG.exchange.api_secret,
                "password": CONFIG.exchange.passphrase,
                "sandbox": CONFIG.exchange.sandbox,
                "timeout": 30000,
                "enableRateLimit": True,
            })
        return self._exchange

    async def scan(self) -> list[MarketSignal]:
        """
        Fetch tickers, rank by 24h change, filter for volume spikes.
        Returns the top N signals sorted by momentum.
        """
        try:
            exchange = await self._get_exchange()
            tickers = await exchange.fetch_tickers()
        except Exception as exc:
            audit(system_log, f"Scanner exchange error: {exc}",
                  action="scan", result="ERROR")
            return []

        signals: list[MarketSignal] = []
        seen_symbols: set[str] = set()
        for symbol, tick in tickers.items():
            if not symbol.endswith("/USDT"):
                continue
            seen_symbols.add(symbol)
            try:
                change = float(tick.get("percentage", 0) or 0)
                volume = float(tick.get("quoteVolume", 0) or 0)
                price = float(tick.get("last", 0) or 0)
                if price <= 0 or volume < 50_000:
                    continue

                spike = self._detect_volume_spike(symbol, volume)
                momentum = self._momentum_score(change, spike)

                signals.append(MarketSignal(
                    symbol=symbol,
                    price=price,
                    change_pct_24h=round(change, 2),
                    volume_usd_24h=round(volume, 2),
                    volume_spike=spike,
                    momentum_score=round(momentum, 3),
                    timestamp=datetime.now(UTC),
                ))
            except (TypeError, ValueError):
                continue

        # Sort by absolute momentum descending
        signals.sort(key=lambda s: abs(s.momentum_score), reverse=True)
        top = signals[: CONFIG.top_movers_count]

        # Evict stale symbols not seen in this scan to cap memory
        with self._lock:
            stale = [s for s in self._volume_history if s not in seen_symbols]
            for s in stale:
                del self._volume_history[s]
            # Hard cap: if still over 500 symbols, trim oldest entries
            if len(self._volume_history) > 500:
                excess = len(self._volume_history) - 500
                for key in list(self._volume_history)[:excess]:
                    del self._volume_history[key]

        audit(system_log, f"Scan complete: {len(top)} signals from {len(signals)} pairs",
              action="scan", result="OK", data={"count": len(top)})
        return top

    # -- Internal helpers --

    def _detect_volume_spike(self, symbol: str, current_vol: float) -> bool:
        """True if current volume is >2x the rolling average."""
        with self._lock:
            history = self._volume_history.setdefault(symbol, [])
            if len(history) >= 5:
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

    async def close(self) -> None:
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
