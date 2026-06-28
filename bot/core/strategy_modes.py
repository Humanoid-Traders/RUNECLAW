"""
RUNECLAW Strategy Modes -- adaptive strategy selection based on regime + context.

Instead of one-size-fits-all analysis, RUNECLAW selects from specialized
strategy modes based on detected market regime and conditions:

  - TREND_CONTINUATION: ride established trends with pullback entries
  - BREAKOUT: capture structural breakouts with volume confirmation
  - MEAN_REVERSION: fade extremes in ranging markets
  - LIQUIDITY_SWEEP: detect stop hunts and trade the reversal

Each mode adjusts: entry logic, SL/TP multipliers, confidence requirements,
and which confluence voters get boosted/suppressed.

Design rules:
  - Strategy mode is a recommendation, not a trade signal
  - Risk engine still has final say on every trade
  - Mode selection is audited and explained
  - Fail-closed: uncertain regime → CONSERVATIVE mode (tighter filters)
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from bot.core.ta_utils import Regime


# ── Strategy Modes ────────────────────────────────────────────────

class StrategyMode(str, Enum):
    TREND_CONTINUATION = "TREND_CONTINUATION"
    BREAKOUT = "BREAKOUT"
    TURTLE_BREAKOUT = "TURTLE_BREAKOUT"
    MEAN_REVERSION = "MEAN_REVERSION"
    LIQUIDITY_SWEEP = "LIQUIDITY_SWEEP"
    CONSERVATIVE = "CONSERVATIVE"  # default / uncertain


class ModeConfig(BaseModel):
    """Per-mode parameter overrides."""
    mode: StrategyMode
    sl_mult: float = 2.5
    tp_mult: float = 3.05
    min_confidence: float = 0.60
    confluence_boost: dict[str, float] = Field(default_factory=dict)
    description: str = ""


# Pre-defined mode configurations
MODE_CONFIGS: dict[StrategyMode, ModeConfig] = {
    StrategyMode.TREND_CONTINUATION: ModeConfig(
        mode=StrategyMode.TREND_CONTINUATION,
        sl_mult=2.0,
        tp_mult=4.0,   # R:R = 2.0 — let winners run in trends
        min_confidence=0.58,
        confluence_boost={
            "mtf_alignment": 1.5,     # HTF alignment is crucial
            "of_cvd_trend": 1.3,      # volume confirming trend
            "smart_money_composite": 1.2,
        },
        description="Ride established trends. Pullback entry, wide TP, "
                    "requires HTF alignment and volume confirmation.",
    ),
    StrategyMode.BREAKOUT: ModeConfig(
        mode=StrategyMode.BREAKOUT,
        sl_mult=1.8,
        tp_mult=3.5,   # R:R ~1.9
        min_confidence=0.65,  # higher bar — breakouts have high false-positive rate
        confluence_boost={
            "mtf_bos": 2.0,            # break of structure is THE signal
            "of_book_imbalance": 1.5,   # order flow confirms
            "whale_accumulation": 1.3,
        },
        description="Capture structural breakouts. Requires BOS on HTF, "
                    "volume confirmation, and book imbalance.",
    ),
    StrategyMode.MEAN_REVERSION: ModeConfig(
        mode=StrategyMode.MEAN_REVERSION,
        sl_mult=1.5,
        tp_mult=2.5,   # R:R ~1.67 — tighter targets in ranges
        min_confidence=0.62,
        confluence_boost={
            "rsi": 1.5,                # RSI extremes are key
            "bb_pct_b": 1.5,           # Bollinger extremes
            "stoch": 1.8,              # Stochastic oversold/overbought is primary
            "of_cvd_divergence": 1.8,  # CVD divergence = absorption = reversal
            "reversal": 1.5,           # Pin bars, capitulation
        },
        description="Fade extremes in ranging markets. Stochastic + RSI/BB extremes, "
                    "CVD divergence, tight SL/TP.",
    ),
    StrategyMode.LIQUIDITY_SWEEP: ModeConfig(
        mode=StrategyMode.LIQUIDITY_SWEEP,
        sl_mult=2.0,
        tp_mult=3.0,   # R:R = 1.5
        min_confidence=0.68,  # highest bar — complex pattern
        confluence_boost={
            "liquidation_cascade": 2.0,   # cascade = sweep
            "smart_money_composite": 1.5,
            "whale_accumulation": 1.5,
        },
        description="Detect stop hunts / liquidity sweeps and trade the "
                    "reversal. Requires cascade risk + whale confirmation.",
    ),
    StrategyMode.CONSERVATIVE: ModeConfig(
        mode=StrategyMode.CONSERVATIVE,
        sl_mult=2.5,
        tp_mult=3.05,
        min_confidence=0.65,
        confluence_boost={},
        description="Default / uncertain regime. Standard parameters with "
                    "higher confidence threshold. Safety-first.",
    ),
    StrategyMode.TURTLE_BREAKOUT: ModeConfig(
        mode=StrategyMode.TURTLE_BREAKOUT,
        sl_mult=2.0,
        tp_mult=4.5,   # R:R = 2.25 — Turtle system lets winners run
        min_confidence=0.60,
        confluence_boost={
            "donchian": 2.0,           # Donchian breakout IS the signal
            "stoch": 1.3,              # Momentum confirmation
            "mtf_alignment": 1.5,      # HTF trend alignment
            "of_cvd_trend": 1.3,       # Volume confirming breakout
        },
        description="Turtle breakout system. 20/55-bar Donchian channel breakout "
                    "with volume confirmation and ATR trailing stop.",
    ),
}


# ── Mode Selection ────────────────────────────────────────────────

class ModeSelection(BaseModel):
    """Result of strategy mode selection."""
    selected_mode: StrategyMode = StrategyMode.CONSERVATIVE
    config: ModeConfig = Field(default_factory=lambda: MODE_CONFIGS[StrategyMode.CONSERVATIVE])
    candidates: list[dict] = Field(default_factory=list)
    reasoning: str = ""
    confidence: float = 0.0


class StrategySelector:
    """Select the optimal strategy mode based on regime + context.

    Usage:
        selector = StrategySelector()
        selection = selector.select(
            regime=Regime.TREND_UP,
            indicators=indicators,
            mtf_result=mtf_result,
            smart_money=smart_money_score,
        )
    """

    def select(
        self,
        regime: Regime,
        indicators: dict,
        mtf_result=None,       # Optional[MTFResult]
        smart_money=None,      # Optional[SmartMoneyScore]
    ) -> ModeSelection:
        """Score each mode and pick the best fit."""
        scores: dict[StrategyMode, float] = {}
        reasons: dict[StrategyMode, str] = {}

        adx = indicators.get("adx", 0)
        rsi = indicators.get("rsi", 50)
        bb_pct_b = indicators.get("bb_pct_b", 0.5)
        bb_width = indicators.get("bb_width", 0)
        stoch_k = indicators.get("stoch_k", 50)
        stoch_d = indicators.get("stoch_d", 50)
        dc_breakout_high = indicators.get("dc_breakout_high", False)
        dc_breakout_low = indicators.get("dc_breakout_low", False)
        dc55_breakout_high = indicators.get("dc55_breakout_high", False)
        dc55_breakout_low = indicators.get("dc55_breakout_low", False)

        # Score: TREND_CONTINUATION
        score_trend = 0.0
        if regime in (Regime.TREND_UP, Regime.TREND_DOWN) and adx > 25:
            score_trend += 0.5
            if adx > 35:
                score_trend += 0.2
            if mtf_result and abs(getattr(mtf_result, "alignment_score", 0)) > 0.4:
                score_trend += 0.3
        scores[StrategyMode.TREND_CONTINUATION] = score_trend
        reasons[StrategyMode.TREND_CONTINUATION] = f"ADX={adx:.0f}, regime={regime.value}"

        # Score: BREAKOUT
        score_breakout = 0.0
        if bb_width < 0.03 and adx < 25:
            score_breakout += 0.3  # compression = potential breakout
        if mtf_result and getattr(mtf_result, "bos_detected", False):
            score_breakout += 0.5  # structural breakout on HTF
        if smart_money and abs(getattr(smart_money, "whale_accumulation", 0)) > 0.3:
            score_breakout += 0.2
        scores[StrategyMode.BREAKOUT] = score_breakout
        reasons[StrategyMode.BREAKOUT] = f"BB_width={bb_width:.4f}, BOS={mtf_result and getattr(mtf_result, 'bos_detected', False)}"

        # Score: MEAN_REVERSION
        score_mr = 0.0
        if regime in (Regime.RANGE, Regime.CHOP):
            score_mr += 0.3
        if rsi > 75 or rsi < 25:
            score_mr += 0.4  # RSI extreme
        if bb_pct_b > 0.95 or bb_pct_b < 0.05:
            score_mr += 0.3  # BB extreme
        # Stochastic extremes boost mean-reversion
        if stoch_k > 80 or stoch_k < 20:
            score_mr += 0.3
        # Stochastic divergence = strong mean-reversion signal
        if indicators.get("stoch_bull_div") or indicators.get("stoch_bear_div"):
            score_mr += 0.3
        # Pin bars / capitulation support reversal thesis
        if indicators.get("pin_bar_bullish") or indicators.get("pin_bar_bearish"):
            score_mr += 0.2
        if indicators.get("capitulation_sell") or indicators.get("capitulation_buy"):
            score_mr += 0.3
        scores[StrategyMode.MEAN_REVERSION] = score_mr
        reasons[StrategyMode.MEAN_REVERSION] = f"RSI={rsi:.0f}, BB%B={bb_pct_b:.2f}, Stoch={stoch_k:.0f}"

        # Score: TURTLE_BREAKOUT
        score_turtle = 0.0
        if dc_breakout_high or dc_breakout_low:
            score_turtle += 0.5   # 20-bar Donchian breakout
        if dc55_breakout_high or dc55_breakout_low:
            score_turtle += 0.3   # 55-bar confirmation
        if regime in (Regime.TREND_UP, Regime.TREND_DOWN):
            score_turtle += 0.2   # trending regime supports breakout
        if regime == Regime.EXPANSION:
            score_turtle += 0.3   # volatility breakout
        # Volume confirmation
        if indicators.get("vol_momentum") == "expanding":
            score_turtle += 0.2
        scores[StrategyMode.TURTLE_BREAKOUT] = score_turtle
        reasons[StrategyMode.TURTLE_BREAKOUT] = f"DC_break={'HIGH' if dc_breakout_high else 'LOW' if dc_breakout_low else 'none'}, DC55={'yes' if (dc55_breakout_high or dc55_breakout_low) else 'no'}"

        # Score: LIQUIDITY_SWEEP
        score_liq = 0.0
        if smart_money:
            cascade = getattr(smart_money, "cascade_risk", 0)
            if cascade > 0.5:
                score_liq += 0.5
            if cascade > 0.7:
                score_liq += 0.3
            whale_acc = abs(getattr(smart_money, "whale_accumulation", 0))
            if whale_acc > 0.3:
                score_liq += 0.2
        scores[StrategyMode.LIQUIDITY_SWEEP] = score_liq
        reasons[StrategyMode.LIQUIDITY_SWEEP] = f"cascade={smart_money and getattr(smart_money, 'cascade_risk', 0):.2f}" if smart_money else "no smart money data"

        # Select best mode (must beat CONSERVATIVE threshold of 0.4)
        best_mode = max(scores, key=scores.get)
        best_score = scores[best_mode]

        if best_score < 0.4:
            best_mode = StrategyMode.CONSERVATIVE
            best_score = 0.0

        config = MODE_CONFIGS[best_mode]

        candidates = [
            {"mode": mode.value, "score": round(s, 2), "reason": reasons.get(mode, "")}
            for mode, s in sorted(scores.items(), key=lambda x: -x[1])
        ]

        return ModeSelection(
            selected_mode=best_mode,
            config=config,
            candidates=candidates,
            reasoning=f"Selected {best_mode.value}: {config.description}",
            confidence=round(min(1.0, best_score), 2),
        )
