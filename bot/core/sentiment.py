"""
Real-time sentiment analysis module for the RUNECLAW confluence scoring model.

Aggregates sentiment from multiple sources — including the **Crypto Fear &
Greed Index** fetched from the alternative.me public API — together with
price/volume-derived signals (momentum, volume trend, volatility), social-
momentum proxy, and funding-rate contrarian logic.  Produces a single
[-1.0, +1.0] confluence vote compatible with the existing 10-voter scoring
model.

Includes contrarian logic: when crowd sentiment reaches extremes the signal
flips direction. Extreme greed produces a bearish vote; extreme fear produces
a bullish vote.  The external Fear & Greed Index is blended into the final
vote as an additional contrarian adjustment (cached for 1 hour).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from bot.compat import UTC
from enum import Enum
from typing import Optional

import aiohttp
import numpy as np
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_HISTORY = 100
_LOOKBACK = 20  # number of data points for rolling calculations

# Fear & Greed component weights
_FG_MOMENTUM_W = 0.40
_FG_VOLUME_W = 0.30
_FG_VOLATILITY_W = 0.30

# Regime thresholds (0-100 scale)
_EXTREME_FEAR_CEIL = 20.0
_FEAR_CEIL = 40.0
_NEUTRAL_CEIL = 60.0
_GREED_CEIL = 80.0

# Contrarian thresholds on the 0-100 fear/greed scale
_CONTRARIAN_GREED_FLOOR = 80.0   # >= this triggers contrarian bearish
_CONTRARIAN_FEAR_CEIL = 20.0     # <= this triggers contrarian bullish

# Funding rate contrarian thresholds
_FUNDING_HIGH = 0.0005   # 0.05 %
_FUNDING_LOW = -0.0005

# Vote bounds
_NORMAL_VOTE_MIN = -0.3
_NORMAL_VOTE_MAX = 0.3
_CONTRARIAN_BULL_MIN = 0.3
_CONTRARIAN_BULL_MAX = 0.6
_CONTRARIAN_BEAR_MIN = -0.6
_CONTRARIAN_BEAR_MAX = -0.3

# Confluence weight assigned to this voter
_CONFLUENCE_WEIGHT = 0.6

# External Fear & Greed Index (alternative.me) cache TTL in seconds
_FG_CACHE_TTL = 3600  # 1 hour

# External Fear & Greed API endpoint
_FG_API_URL = "https://api.alternative.me/fng/?limit=1"

# Maximum contrarian adjustment from external Fear & Greed Index
_EXT_FG_MAX_ADJUSTMENT = 0.3


# ---------------------------------------------------------------------------
# Enums & Models
# ---------------------------------------------------------------------------

class SentimentRegime(str, Enum):
    """Categorical sentiment regime derived from the fear/greed index."""

    EXTREME_FEAR = "EXTREME_FEAR"
    FEAR = "FEAR"
    NEUTRAL = "NEUTRAL"
    GREED = "GREED"
    EXTREME_GREED = "EXTREME_GREED"


class SentimentSnapshot(BaseModel):
    """Point-in-time sentiment reading produced by :class:`SentimentEngine`."""

    fear_greed_index: float = Field(
        ge=0.0, le=100.0,
        description="Composite fear/greed index. 0 = extreme fear, 100 = extreme greed.",
    )
    regime: SentimentRegime = Field(
        description="Categorical regime derived from fear_greed_index.",
    )
    social_momentum: float = Field(
        ge=-1.0, le=1.0,
        description="Social-buzz proxy derived from price velocity. Positive = bullish chatter.",
    )
    funding_sentiment: float = Field(
        ge=-1.0, le=1.0,
        description="Contrarian funding-rate signal. Positive = bullish (funding was negative).",
    )
    composite_score: float = Field(
        ge=-1.0, le=1.0,
        description="Blended sentiment score before contrarian adjustment.",
    )
    confluence_vote: float = Field(
        ge=-1.0, le=1.0,
        description="Final vote fed into the confluence scoring model.",
    )
    is_contrarian_active: bool = Field(
        default=False,
        description="True when the contrarian override is in effect.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="UTC timestamp of this snapshot.",
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class SentimentEngine:
    """
    Real-time sentiment fusion for the RUNECLAW confluence scoring model.

    Combines a simulated fear/greed estimation, a social-momentum proxy, and
    funding-rate contrarian logic into a single ``[-1, +1]`` confluence vote
    that plugs into the existing multi-voter scoring framework.

    **Fear & Greed estimation** (0-100 scale)
        Derived from three components of recent market data:

        * *Price momentum* (40 %) -- normalised rolling return.
        * *Volume trend* (30 %) -- ratio of latest volume to rolling mean.
        * *Volatility* (30 %) -- recent standard-deviation of returns mapped
          inversely (high vol = fear, low vol = greed).

    **Regime mapping** (0-100 to categorical)

        ========== ===============
        Range      Regime
        ========== ===============
        0  - 20    EXTREME_FEAR
        20 - 40    FEAR
        40 - 60    NEUTRAL
        60 - 80    GREED
        80 - 100   EXTREME_GREED
        ========== ===============

    **Contrarian logic**
        When the crowd reaches an extreme the vote flips:

        * ``EXTREME_FEAR``  -> bullish vote in ``[+0.3, +0.6]``
        * ``EXTREME_GREED`` -> bearish vote in ``[-0.6, -0.3]``

        In normal regimes the vote maps linearly to ``[-0.3, +0.3]``.

    **Funding-rate contrarian**
        Extreme positive funding (> 0.05 %) adds a bearish offset.
        Extreme negative funding (< -0.05 %) adds a bullish offset.

    Usage::

        engine = SentimentEngine()
        snap = engine.update("BTCUSDT", price=67_500, volume=1.2e9,
                             funding_rate=0.0003, price_change_pct=2.5)
        vote = engine.get_confluence_vote()
    """

    def __init__(self) -> None:
        self._history: dict[str, list[SentimentSnapshot]] = {}
        self._price_returns: dict[str, list[float]] = {}
        self._volume_history: dict[str, list[float]] = {}
        # External Fear & Greed Index cache
        self._fear_greed_value: Optional[float] = None
        self._fear_greed_ts: float = 0.0  # monotonic timestamp of last fetch

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def _fetch_fear_greed(self) -> Optional[float]:
        """Fetch the Crypto Fear & Greed Index from alternative.me.

        Returns the index value (0-100) or ``None`` on failure.  The result
        is cached in ``self._fear_greed_value`` for ``_FG_CACHE_TTL`` seconds.
        """
        try:
            async with aiohttp.ClientSession(trust_env=True) as session:
                async with session.get(_FG_API_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        logger.warning("Fear & Greed API returned status %s", resp.status)
                        return self._fear_greed_value
                    data = await resp.json()
                    value = float(data["data"][0]["value"])
                    self._fear_greed_value = value
                    self._fear_greed_ts = time.monotonic()
                    logger.info(
                        "Fetched Fear & Greed Index: %.0f (%s)",
                        value,
                        data["data"][0].get("value_classification", ""),
                    )
                    return value
        except Exception:
            logger.warning("Failed to fetch Fear & Greed Index; using cached or None", exc_info=True)
            return self._fear_greed_value

    async def refresh_fear_greed(self) -> Optional[float]:
        """Refresh the external Fear & Greed Index if the cache has expired.

        Safe to call frequently — it is a no-op when the cache is fresh.
        Returns the current cached value (or ``None`` if never fetched).
        """
        now = time.monotonic()
        if self._fear_greed_value is None or (now - self._fear_greed_ts) >= _FG_CACHE_TTL:
            return await self._fetch_fear_greed()
        return self._fear_greed_value

    def update(
        self,
        symbol: str,
        price: float,
        volume: float,
        funding_rate: float = 0.0,
        price_change_pct: float = 0.0,
    ) -> SentimentSnapshot:
        """
        Ingest new market data, recompute sentiment, and return a snapshot.

        Parameters
        ----------
        symbol:
            Trading pair identifier (e.g. ``"BTCUSDT"``).
        price:
            Current market price.
        volume:
            Current-period volume (absolute value; units don't matter as
            long as they are consistent across calls).
        funding_rate:
            Perpetual-swap funding rate.  Typical range is
            ``-0.01 ... +0.01``; the contrarian thresholds fire at
            ``+/-0.0005`` (0.05 %).
        price_change_pct:
            24-hour price change expressed as a percentage (e.g. ``2.5``
            for +2.5 %).

        Returns
        -------
        SentimentSnapshot
            The freshly computed sentiment reading.
        """
        # --- accumulate rolling data ---
        self._price_returns.setdefault(symbol, []).append(price_change_pct)
        self._volume_history.setdefault(symbol, []).append(volume)

        # keep only the most recent _LOOKBACK entries
        self._price_returns[symbol] = self._price_returns[symbol][-_LOOKBACK:]
        self._volume_history[symbol] = self._volume_history[symbol][-_LOOKBACK:]

        returns = np.array(self._price_returns[symbol], dtype=np.float64)
        volumes = np.array(self._volume_history[symbol], dtype=np.float64)

        # --- component scores (each mapped to 0-100) ---
        momentum_score = self._calc_momentum_score(returns)
        volume_score = self._calc_volume_score(volumes)
        volatility_score = self._calc_volatility_score(returns)

        fear_greed = float(np.clip(
            _FG_MOMENTUM_W * momentum_score
            + _FG_VOLUME_W * volume_score
            + _FG_VOLATILITY_W * volatility_score,
            0.0,
            100.0,
        ))

        regime = self._regime_from_index(fear_greed)

        # --- social momentum proxy ---
        social_momentum = self._calc_social_momentum(returns)

        # --- funding-rate contrarian ---
        funding_sentiment = self._calc_funding_sentiment(funding_rate)

        # --- composite (before contrarian) maps FG to [-1, +1] ---
        composite_score = float(np.clip((fear_greed - 50.0) / 50.0, -1.0, 1.0))

        # --- confluence vote (with contrarian logic) ---
        confluence_vote, contrarian_active = self._compute_vote(
            fear_greed, composite_score, funding_sentiment,
        )

        snapshot = SentimentSnapshot(
            fear_greed_index=round(fear_greed, 4),
            regime=regime,
            social_momentum=round(social_momentum, 4),
            funding_sentiment=round(funding_sentiment, 4),
            composite_score=round(composite_score, 4),
            confluence_vote=round(confluence_vote, 4),
            is_contrarian_active=contrarian_active,
        )

        self._history.setdefault(symbol, []).append(snapshot)
        if len(self._history[symbol]) > _MAX_HISTORY:
            self._history[symbol] = self._history[symbol][-_MAX_HISTORY:]

        return snapshot

    def get_confluence_vote(self, symbol: str = "") -> float:
        """
        Return the most recent sentiment vote for the confluence model.

        If *symbol* is given, returns the vote for that specific symbol.
        Otherwise returns the most recent vote across all symbols.
        Returns ``0.0`` (neutral) if no data has been ingested yet.

        When an external Fear & Greed value is available it is blended in
        as a contrarian adjustment (up to +/-0.3).
        """
        if symbol and symbol in self._history and self._history[symbol]:
            base = self._history[symbol][-1].confluence_vote
        else:
            latest = self.latest
            if latest is None:
                return 0.0
            base = latest.confluence_vote

        return float(np.clip(base + self._ext_fg_adjustment(), -1.0, 1.0))

    def _ext_fg_adjustment(self) -> float:
        """Compute the contrarian adjustment from the external Fear & Greed Index.

        * F&G < 25  (Extreme Fear)  -> +0.3  (contrarian bullish)
        * F&G > 75  (Extreme Greed) -> -0.3  (contrarian bearish)
        * 25 <= F&G <= 75           -> linear interpolation through 0
        * No data                   -> 0.0
        """
        if self._fear_greed_value is None:
            return 0.0
        fg = self._fear_greed_value
        # Linear map: 0 -> +0.3, 50 -> 0.0, 100 -> -0.3
        adjustment = -_EXT_FG_MAX_ADJUSTMENT * (fg - 50.0) / 50.0
        return float(np.clip(adjustment, -_EXT_FG_MAX_ADJUSTMENT, _EXT_FG_MAX_ADJUSTMENT))

    def to_confluence_votes(self) -> list[tuple[str, float, float]]:
        """
        Return votes in the format expected by the confluence scorer.

        Returns
        -------
        list[tuple[str, float, float]]
            A single-element list of ``(name, vote, weight)`` tuples.
            ``name`` is ``"sentiment_composite"``, ``vote`` is in
            ``[-1.0, +1.0]``, and ``weight`` is ``0.6``.
        """
        return [("sentiment_composite", self.get_confluence_vote(), _CONFLUENCE_WEIGHT)]

    @property
    def current_regime(self) -> SentimentRegime:
        """
        The sentiment regime of the most recent snapshot.

        Defaults to ``NEUTRAL`` when no data has been ingested.
        """
        latest = self.latest
        if latest is None:
            return SentimentRegime.NEUTRAL
        return latest.regime

    @property
    def latest(self) -> Optional[SentimentSnapshot]:
        """
        The most recent :class:`SentimentSnapshot`, or ``None`` if the
        engine has not yet received any data.
        """
        if not self._history:
            return None
        # Find the most recent snapshot across all symbols
        newest = None
        for snapshots in self._history.values():
            if snapshots:
                candidate = snapshots[-1]
                if newest is None or candidate.timestamp > newest.timestamp:
                    newest = candidate
        return newest

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _calc_momentum_score(returns: np.ndarray) -> float:
        """
        Map rolling mean return to a 0-100 momentum score.

        A mean return of +15 % maps to 100; -15 % maps to 0.  Values
        outside that range are clipped.
        """
        if len(returns) == 0:
            return 50.0
        mean_ret = float(np.mean(returns))
        # linear map: -15 -> 0, +15 -> 100
        score = (mean_ret + 15.0) / 30.0 * 100.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _calc_volume_score(volumes: np.ndarray) -> float:
        """
        Compare latest volume to the rolling mean.

        Ratio > 1.5 maps to 100 (greedy euphoria).  Ratio < 0.5 maps to
        0 (fearful apathy).  Linear interpolation in between.
        """
        if len(volumes) < 2:
            return 50.0
        mean_vol = float(np.mean(volumes[:-1]))
        if mean_vol <= 0:
            return 50.0
        ratio = float(volumes[-1]) / mean_vol
        # linear map: 0.5 -> 0, 1.5 -> 100
        score = (ratio - 0.5) / 1.0 * 100.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _calc_volatility_score(returns: np.ndarray) -> float:
        """
        Map rolling volatility to a 0-100 score (inverted: high vol = fear).

        Std-dev of 0 maps to 100 (calm/greedy), std-dev of 15 maps to 0
        (fearful).
        """
        if len(returns) < 2:
            return 50.0
        std = float(np.std(returns, ddof=1))
        # linear map: 0 -> 100, 15 -> 0
        score = (1.0 - std / 15.0) * 100.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _calc_social_momentum(returns: np.ndarray) -> float:
        """
        Proxy social buzz using price velocity (mean of recent returns).

        Returns a value in ``[-1.0, +1.0]``.
        """
        if len(returns) == 0:
            return 0.0
        mean_ret = float(np.mean(returns))
        # map +/-10 % mean return to +/-1.0
        return float(np.clip(mean_ret / 10.0, -1.0, 1.0))

    @staticmethod
    def _calc_funding_sentiment(funding_rate: float) -> float:
        """
        Contrarian funding-rate signal.

        * Funding above +0.05 % -> bearish (-0.2 to -1.0 linearly).
        * Funding below -0.05 % -> bullish (+0.2 to +1.0 linearly).
        * In between -> 0.0 (no signal).
        """
        if funding_rate > _FUNDING_HIGH:
            # scale linearly: 0.0005 -> -0.2, 0.005 -> -1.0
            raw = -0.2 - 0.8 * (funding_rate - _FUNDING_HIGH) / (0.005 - _FUNDING_HIGH)
            return float(np.clip(raw, -1.0, -0.2))
        if funding_rate < _FUNDING_LOW:
            raw = 0.2 + 0.8 * (_FUNDING_LOW - funding_rate) / (_FUNDING_LOW - (-0.005))
            return float(np.clip(raw, 0.2, 1.0))
        return 0.0

    @staticmethod
    def _regime_from_index(fg: float) -> SentimentRegime:
        """Map a 0-100 fear/greed index to a categorical regime."""
        if fg < _EXTREME_FEAR_CEIL:
            return SentimentRegime.EXTREME_FEAR
        if fg < _FEAR_CEIL:
            return SentimentRegime.FEAR
        if fg < _NEUTRAL_CEIL:
            return SentimentRegime.NEUTRAL
        if fg < _GREED_CEIL:
            return SentimentRegime.GREED
        return SentimentRegime.EXTREME_GREED

    @staticmethod
    def _compute_vote(
        fear_greed: float,
        composite: float,
        funding_sentiment: float,
    ) -> tuple[float, bool]:
        """
        Derive the final confluence vote and contrarian flag.

        Parameters
        ----------
        fear_greed:
            The 0-100 fear/greed index.
        composite:
            The pre-contrarian composite in ``[-1, +1]``.
        funding_sentiment:
            The contrarian funding signal in ``[-1, +1]``.

        Returns
        -------
        tuple[float, bool]
            ``(confluence_vote, is_contrarian_active)``
        """
        contrarian = False

        if fear_greed <= _CONTRARIAN_FEAR_CEIL:
            # Extreme fear -> bullish contrarian vote
            # Linearly map: fg=20 -> +0.3, fg=0 -> +0.6
            intensity = (_CONTRARIAN_FEAR_CEIL - fear_greed) / _CONTRARIAN_FEAR_CEIL
            vote = _CONTRARIAN_BULL_MIN + intensity * (
                _CONTRARIAN_BULL_MAX - _CONTRARIAN_BULL_MIN
            )
            contrarian = True
        elif fear_greed >= _CONTRARIAN_GREED_FLOOR:
            # Extreme greed -> bearish contrarian vote
            # Linearly map: fg=80 -> -0.3, fg=100 -> -0.6
            intensity = (fear_greed - _CONTRARIAN_GREED_FLOOR) / (
                100.0 - _CONTRARIAN_GREED_FLOOR
            )
            vote = _CONTRARIAN_BEAR_MAX + intensity * (
                _CONTRARIAN_BEAR_MIN - _CONTRARIAN_BEAR_MAX
            )
            contrarian = True
        else:
            # Normal regime: linear map of composite [-1,+1] -> [-0.3, +0.3]
            vote = composite * _NORMAL_VOTE_MAX

        # Blend in funding contrarian (additive, then clamp)
        vote += funding_sentiment * 0.2

        vote = float(np.clip(vote, -1.0, 1.0))
        return vote, contrarian


# ---------------------------------------------------------------------------
# SentimentAnalyzer — lightweight static/class-method interface
# ---------------------------------------------------------------------------

class SentimentAnalyzer:
    """Static-method sentiment helpers for funding rate, long/short ratio,
    and composite sentiment scoring.  Designed for the intelligence-layer
    upgrade (task 12).
    """

    @staticmethod
    def analyze_funding_rate(funding_rate: float) -> dict:
        """Contrarian analysis of perpetual funding rate.

        Parameters
        ----------
        funding_rate : float
            Funding rate as a percentage (e.g. 0.05 means 0.05 %).

        Returns
        -------
        dict  with keys bias, signal_strength, interpretation.
        """
        if funding_rate > 0.03:
            bias = "BEARISH"
            interpretation = "Crowded longs — contrarian bearish"
        elif funding_rate < -0.03:
            bias = "BULLISH"
            interpretation = "Crowded shorts — contrarian bullish"
        else:
            bias = "NEUTRAL"
            interpretation = "Funding rate within normal range"

        signal_strength = min(1.0, abs(funding_rate) / 0.1)
        return {
            "bias": bias,
            "signal_strength": round(signal_strength, 4),
            "interpretation": interpretation,
        }

    @staticmethod
    def analyze_long_short_ratio(ratio: float) -> dict:
        """Contrarian analysis of the aggregate long/short ratio.

        Parameters
        ----------
        ratio : float
            Long/short ratio (e.g. 2.5 = 2.5x more longs than shorts).

        Returns
        -------
        dict  with keys bias, signal_strength, interpretation.
        """
        if ratio > 2.0:
            bias = "BEARISH"
            strength = min(1.0, (ratio - 2.0) / 3.0)
            interpretation = "Too many longs — contrarian bearish"
        elif ratio < 0.5:
            bias = "BULLISH"
            strength = min(1.0, (0.5 - ratio) / 0.5)
            interpretation = "Too many shorts — contrarian bullish"
        else:
            bias = "NEUTRAL"
            strength = 0.0
            interpretation = "Long/short ratio balanced"

        return {
            "bias": bias,
            "signal_strength": round(strength, 4),
            "interpretation": interpretation,
        }

    @staticmethod
    def composite_sentiment(
        funding_rate: float,
        long_short_ratio: float = 1.0,
        fear_greed: int = 50,
    ) -> dict:
        """Weighted composite of all sentiment signals.

        Returns
        -------
        dict  with keys overall_bias, score (-1 to 1), signals, contrarian_alert.
        """
        funding = SentimentAnalyzer.analyze_funding_rate(funding_rate)
        ls = SentimentAnalyzer.analyze_long_short_ratio(long_short_ratio)

        # Map biases to numeric scores
        bias_map = {"BULLISH": 1.0, "NEUTRAL": 0.0, "BEARISH": -1.0}

        funding_score = bias_map[funding["bias"]] * funding["signal_strength"]
        ls_score = bias_map[ls["bias"]] * ls["signal_strength"]

        # Fear & Greed: map 0-100 to -1..+1, then apply contrarian at extremes
        fg_raw = (fear_greed - 50) / 50.0
        fg_contrarian = False
        if fear_greed >= 80:
            fg_score = -abs(fg_raw)  # extreme greed -> bearish
            fg_contrarian = True
        elif fear_greed <= 20:
            fg_score = abs(fg_raw)  # extreme fear -> bullish
            fg_contrarian = True
        else:
            fg_score = fg_raw * 0.3  # normal regime, muted

        # Weighted average: funding 40%, L/S 35%, fear/greed 25%
        composite = 0.40 * funding_score + 0.35 * ls_score + 0.25 * fg_score
        composite = max(-1.0, min(1.0, composite))

        # Overall bias label
        if composite > 0.15:
            overall = "BULLISH"
        elif composite < -0.15:
            overall = "BEARISH"
        else:
            overall = "NEUTRAL"

        # Contrarian alert: extreme one-sided positioning
        contrarian_alert = (
            funding["signal_strength"] > 0.5
            or ls["signal_strength"] > 0.5
            or fg_contrarian
        )

        signals = [
            f"Funding: {funding['bias']} ({funding['signal_strength']:.2f})",
            f"L/S Ratio: {ls['bias']} ({ls['signal_strength']:.2f})",
            f"Fear/Greed: {fear_greed}",
        ]

        return {
            "overall_bias": overall,
            "score": round(composite, 4),
            "signals": signals,
            "contrarian_alert": contrarian_alert,
        }

    @staticmethod
    def format_for_telegram(result: dict) -> str:
        """Format a composite sentiment result as Telegram-compatible HTML."""
        bias = result.get("overall_bias", "NEUTRAL")
        score = result.get("score", 0.0)
        signals = result.get("signals", [])
        alert = result.get("contrarian_alert", False)

        bias_icon = {"BULLISH": "^", "BEARISH": "v", "NEUTRAL": "-"}.get(bias, "-")
        lines = [
            "<b>SENTIMENT WAR ROOM</b>",
            f"<b>Bias:</b> {bias} [{bias_icon}]  Score: {score:+.2f}",
            "",
        ]
        for sig in signals:
            lines.append(f"  {sig}")
        if alert:
            lines.append("")
            lines.append("<b>CONTRARIAN ALERT:</b> Extreme positioning detected")
        return "\n".join(lines)
