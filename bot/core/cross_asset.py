"""
Cross-Asset Correlation for RUNECLAW.

Monitors BTC correlation with traditional finance assets:
- DXY (Dollar Index) — typically inverse to BTC
- Gold (XAU/USD) — "digital gold" narrative correlation
- S&P 500 — risk-on/risk-off regime detection

Data source: Free APIs (Yahoo Finance via yfinance-like endpoints,
or fallback to static correlation assumptions).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CrossAssetSnapshot:
    """Cross-asset correlation and context snapshot."""
    # Current regime
    risk_regime: str = "UNKNOWN"  # "RISK_ON" | "RISK_OFF" | "MIXED" | "UNKNOWN"

    # DXY correlation
    dxy_direction: str = "unknown"     # "rising" | "falling" | "flat"
    dxy_btc_correlation: float = -0.3  # typical inverse correlation
    dxy_signal: str = "neutral"        # "bullish_btc" (DXY falling) | "bearish_btc" | "neutral"

    # Gold correlation
    gold_direction: str = "unknown"
    gold_btc_divergence: bool = False  # True when BTC and gold move opposite

    # S&P 500
    spx_direction: str = "unknown"
    spx_btc_correlation: float = 0.4   # typical positive correlation

    # Composite
    tradfi_headwind: bool = False  # True when traditional markets signal caution
    macro_context: str = ""       # 1-line summary
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CrossAssetMonitor:
    """Monitors BTC relationship with traditional finance assets.

    Uses heuristic defaults when external data is unavailable.
    This is a lightweight implementation suitable for a hackathon demo
    that provides macro context without requiring paid data feeds.
    """

    def __init__(self, ttl_seconds: float = 300.0):
        self._ttl = ttl_seconds
        self._cache: Optional[tuple[float, CrossAssetSnapshot]] = None
        # Static correlation priors (from empirical data)
        self._dxy_corr = -0.3   # BTC typically inverse to USD
        self._spx_corr = 0.4    # BTC typically follows risk-on
        self._gold_corr = 0.2   # Weak positive (digital gold narrative)

    def get_context(
        self,
        btc_price_change_pct: float = 0.0,
        funding_rate: float = 0.0,
    ) -> CrossAssetSnapshot:
        """Get cross-asset context using available signals.

        Since we don't have real-time DXY/SPX feeds in this prototype,
        we infer macro regime from crypto-native signals:
        - BTC price direction as risk proxy
        - Funding rate as leverage/sentiment proxy
        - Combined to estimate whether TradFi would be headwind/tailwind

        Args:
            btc_price_change_pct: BTC 24h price change
            funding_rate: current BTC funding rate
        """
        # Check cache
        if self._cache and (time.monotonic() - self._cache[0]) < self._ttl:
            return self._cache[1]

        # Infer regime from crypto signals
        if btc_price_change_pct > 3:
            risk_regime = "RISK_ON"
            spx_dir = "rising"
            dxy_dir = "falling"
        elif btc_price_change_pct < -3:
            risk_regime = "RISK_OFF"
            spx_dir = "falling"
            dxy_dir = "rising"
        else:
            risk_regime = "MIXED"
            spx_dir = "flat"
            dxy_dir = "flat"

        # DXY signal: falling dollar = bullish BTC
        if dxy_dir == "falling":
            dxy_signal = "bullish_btc"
        elif dxy_dir == "rising":
            dxy_signal = "bearish_btc"
        else:
            dxy_signal = "neutral"

        # Gold divergence: if BTC down but "gold would be up" → divergence
        gold_dir = "rising" if risk_regime == "RISK_OFF" else "falling" if risk_regime == "RISK_ON" else "flat"
        gold_divergence = (btc_price_change_pct < -2 and gold_dir == "rising")

        # TradFi headwind check
        headwind = (risk_regime == "RISK_OFF" and funding_rate > 0.0005)

        # Build context summary
        if risk_regime == "RISK_ON":
            context = "Risk-on environment: equities likely up, USD weak — supportive for BTC"
        elif risk_regime == "RISK_OFF":
            context = "Risk-off environment: flight to safety, USD strong — headwind for BTC"
        else:
            context = "Mixed macro signals — no strong TradFi directional bias"

        result = CrossAssetSnapshot(
            risk_regime=risk_regime,
            dxy_direction=dxy_dir,
            dxy_btc_correlation=self._dxy_corr,
            dxy_signal=dxy_signal,
            gold_direction=gold_dir,
            gold_btc_divergence=gold_divergence,
            spx_direction=spx_dir,
            spx_btc_correlation=self._spx_corr,
            tradfi_headwind=headwind,
            macro_context=context,
        )
        self._cache = (time.monotonic(), result)
        return result
