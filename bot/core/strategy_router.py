"""
RUNECLAW Strategy Router — selects optimal strategy based on market regime.

Instead of one strategy with regime penalties, this router maintains
separate strategy profiles and activates the best one for current conditions:

  - TrendFollower: for TREND_UP/TREND_DOWN — rides momentum, wide TP
  - MeanReversion: for RANGE — fade extremes, tight TP, quick exits
  - BreakoutCatcher: for CHOP transitioning to TREND — catch breakouts
  - MomentumScalp: for high-volatility events — quick in/out

Each strategy has its own:
  - Entry logic preferences (indicator weights)
  - SL/TP ratio profile
  - Position sizing rules
  - Confidence thresholds
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class StrategyProfile:
    """Configuration profile for a specific strategy type."""
    name: str
    strategy_type: str         # "swing", "intraday", "scalp", "position"
    min_confidence: float      # minimum confidence to take a trade
    sl_atr_mult: float         # stop loss distance in ATR multiples
    tp_atr_mult: float         # take profit distance in ATR multiples
    size_multiplier: float     # position size adjustment (1.0 = normal)
    max_trades_concurrent: int # max simultaneous trades for this strategy
    preferred_indicators: list[str]  # indicators to weight more heavily
    confidence_boost: float    # bonus confidence for regime alignment
    description: str


# Strategy profiles keyed by regime
_STRATEGIES: dict[str, StrategyProfile] = {
    "trend_follow": StrategyProfile(
        name="Trend Follower",
        strategy_type="swing",
        min_confidence=0.60,
        sl_atr_mult=2.5,
        tp_atr_mult=4.0,     # wide TP to let winners run
        size_multiplier=1.2,  # larger size in trending markets
        max_trades_concurrent=3,
        preferred_indicators=["ema_cross", "adx", "macd", "elliott_impulse"],
        confidence_boost=0.05,
        description="Rides strong trends with wide TP targets",
    ),
    "mean_reversion": StrategyProfile(
        name="Mean Reversion",
        strategy_type="intraday",
        min_confidence=0.65,
        sl_atr_mult=1.5,     # tight stops
        tp_atr_mult=2.0,     # modest TP
        size_multiplier=0.8, # smaller size in choppy markets
        max_trades_concurrent=2,
        preferred_indicators=["rsi", "bollinger", "volume_profile", "divergence"],
        confidence_boost=0.03,
        description="Fades extremes in range-bound markets",
    ),
    "breakout": StrategyProfile(
        name="Breakout Catcher",
        strategy_type="intraday",
        min_confidence=0.70,  # higher confidence needed for breakouts
        sl_atr_mult=2.0,
        tp_atr_mult=3.5,
        size_multiplier=0.9,
        max_trades_concurrent=2,
        preferred_indicators=["volume_spike", "atr", "bollinger_squeeze", "chart_patterns"],
        confidence_boost=0.04,
        description="Catches breakouts from consolidation",
    ),
    "momentum_scalp": StrategyProfile(
        name="Momentum Scalp",
        strategy_type="scalp",
        min_confidence=0.60,
        sl_atr_mult=1.0,     # very tight stops
        tp_atr_mult=1.5,     # quick targets
        size_multiplier=0.6, # small size for scalps
        max_trades_concurrent=1,
        preferred_indicators=["rsi", "macd", "volume", "order_flow"],
        confidence_boost=0.02,
        description="Quick in/out during high-volatility events",
    ),
}

# Regime to strategy mapping
_REGIME_STRATEGY_MAP: dict[str, str] = {
    "STRONG_TREND_UP": "trend_follow",
    "TREND_UP": "trend_follow",
    "STRONG_TREND_DOWN": "trend_follow",
    "TREND_DOWN": "trend_follow",
    "RANGE": "mean_reversion",
    "CHOP": "breakout",
    "VOLATILE": "momentum_scalp",
    "UNKNOWN": "trend_follow",  # default
}


def select_strategy(
    regime: str,
    volatility_state: str = "NORMAL",
    adx: float = 0.0,
) -> StrategyProfile:
    """Select the optimal strategy for the current market regime.

    Args:
        regime: current market regime from regime detector
        volatility_state: "HIGH", "LOW", or "NORMAL"
        adx: current ADX value

    Returns:
        StrategyProfile for the recommended strategy
    """
    # High volatility override: use scalp strategy regardless of regime
    if volatility_state == "HIGH" and adx < 20:
        return _STRATEGIES["momentum_scalp"]

    # Low ADX in trending regime: probably false trend, use mean reversion
    if regime in ("TREND_UP", "TREND_DOWN") and adx < 15:
        return _STRATEGIES["mean_reversion"]

    # Look up regime mapping
    strategy_key = _REGIME_STRATEGY_MAP.get(regime, "trend_follow")
    return _STRATEGIES[strategy_key]


def get_strategy_adjustments(
    profile: StrategyProfile,
    base_sl_mult: float,
    base_tp_mult: float,
    base_confidence: float,
) -> dict:
    """Get adjusted parameters based on the selected strategy.

    Returns dict with adjusted SL/TP multipliers, confidence, and sizing.
    """
    return {
        "sl_atr_mult": profile.sl_atr_mult,
        "tp_atr_mult": profile.tp_atr_mult,
        "confidence_boost": profile.confidence_boost,
        "size_multiplier": profile.size_multiplier,
        "strategy_type": profile.strategy_type,
        "strategy_name": profile.name,
        "min_confidence": profile.min_confidence,
        "preferred_indicators": profile.preferred_indicators,
    }


def strategy_summary(profile: StrategyProfile) -> str:
    """Human-readable summary of the active strategy."""
    return (
        f"{profile.name} ({profile.strategy_type}) — "
        f"SL={profile.sl_atr_mult}x ATR, TP={profile.tp_atr_mult}x ATR, "
        f"Size={profile.size_multiplier:.0%}, Min Conf={profile.min_confidence:.0%}"
    )
