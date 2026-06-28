"""
RUNECLAW Cross-Asset Correlation Signals — macro context from related markets.

Tracks:
  - BTC Dominance (BTC.D): rising = risk-off rotation (bearish alts)
  - ETH/BTC ratio: rising = alt season signal, falling = BTC dominance
  - DXY (Dollar Index) proxy: strong dollar = crypto headwind
  - Correlation regime: high BTC-alt correlation = risk-on/off, low = divergence

Trading applications:
  - BTC.D rising + alt trade → reduce confidence/size
  - DXY dropping sharply → bullish macro tailwind for crypto
  - ETH/BTC rising → favor alt longs over BTC longs
  - High cross-correlation → concentrated risk, reduce positions
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CrossAssetContext:
    """Current cross-asset market context."""
    btc_dominance_trend: str = "neutral"       # "rising", "falling", "neutral"
    btc_dominance_change_1h: float = 0.0       # % change in BTC dominance over 1h
    eth_btc_trend: str = "neutral"             # "rising", "falling", "neutral"
    eth_btc_ratio: float = 0.0                 # current ETH/BTC price
    dxy_proxy_trend: str = "neutral"           # "strengthening", "weakening", "neutral"
    alt_correlation: float = 0.0               # average alt-BTC correlation (0-1)
    market_regime: str = "normal"              # "risk_on", "risk_off", "rotation", "normal"
    confidence_adjustment: float = 0.0         # adjustment to apply to trade confidence
    size_multiplier: float = 1.0               # position size multiplier
    description: str = ""
    timestamp: float = 0.0


class CrossAssetTracker:
    """Tracks cross-asset signals from exchange data.

    Uses available OHLCV data from the exchange to compute:
    - BTC dominance proxy (BTC market cap share via price ratios)
    - ETH/BTC trend
    - Alt-BTC correlation

    Does NOT require external APIs — all computed from exchange data.
    """

    def __init__(self) -> None:
        self._price_history: dict[str, list[tuple[float, float]]] = {}  # symbol -> [(ts, price)]
        self._btc_prices: list[tuple[float, float]] = []  # [(ts, price)]
        self._eth_prices: list[tuple[float, float]] = []
        self._last_context: Optional[CrossAssetContext] = None
        self._last_update: float = 0
        self._update_interval: float = 300  # recompute every 5 min
        self._max_history: int = 200

    def feed_price(self, symbol: str, price: float, ts: Optional[float] = None) -> None:
        """Feed a price update for cross-asset tracking."""
        t = ts or time.time()

        if symbol not in self._price_history:
            self._price_history[symbol] = []
        self._price_history[symbol].append((t, price))

        # Cap history
        if len(self._price_history[symbol]) > self._max_history:
            self._price_history[symbol] = self._price_history[symbol][-self._max_history:]

        # Track BTC and ETH separately for quick access
        if "BTC" in symbol and "ETH" not in symbol:
            self._btc_prices.append((t, price))
            if len(self._btc_prices) > self._max_history:
                self._btc_prices = self._btc_prices[-self._max_history:]
        elif "ETH" in symbol and "BTC" not in symbol:
            self._eth_prices.append((t, price))
            if len(self._eth_prices) > self._max_history:
                self._eth_prices = self._eth_prices[-self._max_history:]

    def get_context(self, force: bool = False) -> CrossAssetContext:
        """Get current cross-asset context. Cached for performance."""
        now = time.time()
        if not force and self._last_context and (now - self._last_update) < self._update_interval:
            return self._last_context

        ctx = self._compute_context()
        self._last_context = ctx
        self._last_update = now
        return ctx

    def _compute_context(self) -> CrossAssetContext:
        """Compute cross-asset context from price history."""
        ctx = CrossAssetContext(timestamp=time.time())

        # ETH/BTC ratio and trend
        if len(self._btc_prices) >= 10 and len(self._eth_prices) >= 10:
            btc_recent = [p for _, p in self._btc_prices[-20:]]
            eth_recent = [p for _, p in self._eth_prices[-20:]]

            min_len = min(len(btc_recent), len(eth_recent))
            if min_len >= 5:
                ratios = [eth_recent[i] / btc_recent[i] for i in range(min_len) if btc_recent[i] > 0]
                if len(ratios) >= 5:
                    ctx.eth_btc_ratio = round(ratios[-1], 6)
                    early_avg = np.mean(ratios[:len(ratios)//2])
                    late_avg = np.mean(ratios[len(ratios)//2:])

                    change_pct = (late_avg - early_avg) / early_avg * 100 if early_avg > 0 else 0
                    if change_pct > 0.5:
                        ctx.eth_btc_trend = "rising"
                    elif change_pct < -0.5:
                        ctx.eth_btc_trend = "falling"

        # BTC dominance proxy: compare BTC performance vs alt average
        if len(self._btc_prices) >= 10:
            btc_returns = self._compute_returns([p for _, p in self._btc_prices[-20:]])

            alt_returns_list = []
            for symbol, history in self._price_history.items():
                if "BTC" in symbol or len(history) < 10:
                    continue
                prices = [p for _, p in history[-20:]]
                ret = self._compute_returns(prices)
                if ret is not None:
                    alt_returns_list.append(ret)

            if btc_returns is not None and len(alt_returns_list) >= 3:
                avg_alt_return = np.mean(alt_returns_list)
                dom_change = btc_returns - avg_alt_return
                ctx.btc_dominance_change_1h = round(dom_change * 100, 3)

                if dom_change > 0.005:  # BTC outperforming alts by 0.5%+
                    ctx.btc_dominance_trend = "rising"
                elif dom_change < -0.005:
                    ctx.btc_dominance_trend = "falling"

        # Alt-BTC correlation
        if len(self._btc_prices) >= 20:
            btc_prices = np.array([p for _, p in self._btc_prices[-30:]])
            if len(btc_prices) >= 10:
                btc_rets = np.diff(btc_prices) / btc_prices[:-1]

                correlations = []
                for symbol, history in self._price_history.items():
                    if "BTC" in symbol or len(history) < 20:
                        continue
                    alt_prices = np.array([p for _, p in history[-30:]])
                    min_l = min(len(btc_rets), len(alt_prices) - 1)
                    if min_l >= 8:
                        alt_rets = np.diff(alt_prices[-min_l-1:]) / alt_prices[-min_l-1:-1]
                        btc_slice = btc_rets[-min_l:]
                        try:
                            corr = float(np.corrcoef(btc_slice, alt_rets)[0, 1])
                            if not np.isnan(corr):
                                correlations.append(abs(corr))
                        except Exception:
                            pass

                if correlations:
                    ctx.alt_correlation = round(float(np.mean(correlations)), 3)

        # Determine market regime
        ctx.market_regime = self._classify_regime(ctx)

        # Compute adjustments
        ctx.confidence_adjustment, ctx.size_multiplier = self._compute_adjustments(ctx)

        # Description
        parts = []
        if ctx.btc_dominance_trend != "neutral":
            parts.append(f"BTC.D {ctx.btc_dominance_trend} ({ctx.btc_dominance_change_1h:+.2f}%)")
        if ctx.eth_btc_trend != "neutral":
            parts.append(f"ETH/BTC {ctx.eth_btc_trend}")
        if ctx.alt_correlation > 0.7:
            parts.append(f"High alt-BTC corr ({ctx.alt_correlation:.2f})")
        parts.append(f"Regime: {ctx.market_regime}")
        ctx.description = " | ".join(parts) if parts else "Insufficient data"

        return ctx

    def _compute_returns(self, prices: list[float]) -> Optional[float]:
        """Compute total return over a price series."""
        if len(prices) < 2 or prices[0] <= 0:
            return None
        return (prices[-1] - prices[0]) / prices[0]

    def _classify_regime(self, ctx: CrossAssetContext) -> str:
        """Classify market regime from cross-asset signals."""
        if ctx.btc_dominance_trend == "rising" and ctx.alt_correlation > 0.8:
            return "risk_off"  # flight to BTC, alts dumping in correlation
        if ctx.btc_dominance_trend == "falling" and ctx.eth_btc_trend == "rising":
            return "risk_on"  # alt season, money flowing to alts
        if ctx.btc_dominance_trend == "rising" and ctx.eth_btc_trend == "falling":
            return "rotation"  # money rotating from alts to BTC
        return "normal"

    def _compute_adjustments(self, ctx: CrossAssetContext) -> tuple[float, float]:
        """Compute confidence and size adjustments from cross-asset context.

        Returns (confidence_adj, size_mult).
        """
        conf_adj = 0.0
        size_mult = 1.0

        # Risk-off regime: reduce alt exposure
        if ctx.market_regime == "risk_off":
            conf_adj -= 0.05
            size_mult *= 0.7
        elif ctx.market_regime == "risk_on":
            conf_adj += 0.03
            # Don't increase size, just boost confidence
        elif ctx.market_regime == "rotation":
            conf_adj -= 0.03
            size_mult *= 0.85

        # High correlation = concentrated risk
        if ctx.alt_correlation > 0.85:
            size_mult *= 0.8

        return round(conf_adj, 3), round(size_mult, 2)

    def get_symbol_adjustment(self, symbol: str, direction: str) -> tuple[float, float]:
        """Get confidence/size adjustment for a specific symbol trade.

        Returns (confidence_adj, size_mult) considering the symbol type.
        """
        ctx = self.get_context()
        conf_adj = ctx.confidence_adjustment
        size_mult = ctx.size_multiplier

        is_alt = "BTC" not in symbol
        is_long = direction == "LONG"

        # BTC dominance rising + alt long = extra penalty
        if is_alt and is_long and ctx.btc_dominance_trend == "rising":
            conf_adj -= 0.03
            size_mult *= 0.85

        # BTC dominance falling + alt long = boost
        if is_alt and is_long and ctx.btc_dominance_trend == "falling":
            conf_adj += 0.02

        # ETH/BTC rising + ETH long = boost
        if "ETH" in symbol and is_long and ctx.eth_btc_trend == "rising":
            conf_adj += 0.02

        return round(conf_adj, 3), round(size_mult, 2)
