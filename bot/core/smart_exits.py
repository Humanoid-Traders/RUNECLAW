"""
RUNECLAW Smart Exits & Entries — time-based exits, volatility squeeze, adaptive limits.

Features:
  1. Time-Based Exit: close dead trades that haven't moved in N candles
  2. Volatility Squeeze: detect Bollinger Band squeezes for breakout timing
  3. Adaptive Limit Distance: learn optimal limit offset per symbol from fill history
  4. Candle Close Confirmation: wait for candle close before acting on signals
  5. Time-of-Day Edge Filter: track win rate by hour for each symbol
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. Time-Based Exit
# ═══════════════════════════════════════════════════════════════════

@dataclass
class TimeExitConfig:
    """Time-based exit thresholds by strategy type."""
    scalp_candles: int = 3       # close after 3 candles with < 0.5R move
    intraday_candles: int = 12
    swing_candles: int = 48
    position_candles: int = 96
    min_r_progress: float = 0.5  # must reach this R-multiple to stay alive


def should_time_exit(
    strategy_type: str,
    candles_held: int,
    current_r_multiple: float,
    config: Optional[TimeExitConfig] = None,
) -> tuple[bool, str]:
    """Check if a trade should be closed due to lack of progress.

    Args:
        strategy_type: "scalp", "intraday", "swing", "position"
        candles_held: how many candles since entry
        current_r_multiple: current unrealized PnL in R-multiples

    Returns:
        (should_exit, reason)
    """
    cfg = config or TimeExitConfig()

    # No-touch period: don't evaluate exits too early
    if is_in_no_touch_period(strategy_type, candles_held):
        return False, ""

    thresholds = {
        "scalp": cfg.scalp_candles,
        "intraday": cfg.intraday_candles,
        "swing": cfg.swing_candles,
        "position": cfg.position_candles,
    }

    max_candles = thresholds.get(strategy_type, cfg.swing_candles)

    if candles_held >= max_candles and current_r_multiple < cfg.min_r_progress:
        return True, (
            f"Time exit: {candles_held} candles held, "
            f"R={current_r_multiple:.2f} < {cfg.min_r_progress} threshold "
            f"(max {max_candles} for {strategy_type})"
        )

    return False, ""


# Minimum hold periods: don't evaluate exits until enough candles pass
_MIN_HOLD_CANDLES = {
    "scalp": 1,       # at least 1 candle before evaluating
    "intraday": 2,    # 2 candles (2H on 1H chart)
    "swing": 3,       # 3 candles before touching
    "position": 6,    # 6 candles minimum
}


def is_in_no_touch_period(
    strategy_type: str,
    candles_held: int,
) -> bool:
    """Check if position is still in the no-touch period.

    Positions should not be interfered with during the first 1-2 candles
    of the setup timeframe. Acting on first-candle noise leads to
    premature exits and whipsaw losses.

    Returns True if the position should NOT be touched yet.
    """
    min_candles = _MIN_HOLD_CANDLES.get(strategy_type, 2)
    return candles_held < min_candles


def should_volume_decay_exit(
    signal_source: str,
    candles_held: int,
    current_r_multiple: float,
    entry_volume_ratio: float = 1.0,
) -> tuple[bool, str]:
    """Check if a volume-spike-driven trade should exit due to signal decay.

    Volume breakout signals have a short shelf life. If price hasn't
    followed through within 2 candles, the signal is dead — exit regardless
    of the preset hold window.

    Args:
        signal_source: signal type identifier
        candles_held: candles since entry
        current_r_multiple: current unrealized R
        entry_volume_ratio: volume spike magnitude at entry

    Returns:
        (should_exit, reason)
    """
    # Only applies to volume-spike-driven entries
    volume_signals = {"volume_spike", "vol_breakout", "capitulation_buy",
                      "capitulation_sell", "vol_expansion"}

    if signal_source not in volume_signals:
        return False, ""

    # Volume signals decay after 2 candles with no follow-through
    if candles_held >= 2 and current_r_multiple < 0.3:
        return True, (
            f"Volume signal decay: {signal_source} had {candles_held} candles "
            f"with only {current_r_multiple:.2f}R progress (need 0.3R by candle 2)"
        )

    # By candle 4 with sub-1R, the setup is stale
    if candles_held >= 4 and current_r_multiple < 1.0:
        return True, (
            f"Volume signal stale: {candles_held} candles, "
            f"R={current_r_multiple:.2f} (expected 1R+ by candle 4)"
        )

    return False, ""


def funding_cost_warning(
    holding_hours: float,
    funding_rate_8h: float,
    leverage: float,
    current_r_multiple: float,
    strategy_type: str,
) -> tuple[bool, str]:
    """Check if accumulated funding cost is eating into position profit.

    For swing and position trades, funding drag is a real cost.
    If cumulative funding > 50% of current unrealized PnL, warn/exit.

    Args:
        holding_hours: hours since entry
        funding_rate_8h: current 8H funding rate (e.g., 0.01 = 1%)
        leverage: position leverage
        current_r_multiple: current unrealized R
        strategy_type: trade type

    Returns:
        (should_warn, message) — True if funding is concerning
    """
    if strategy_type in ("scalp", "intraday"):
        return False, ""  # too short for funding to matter

    if funding_rate_8h == 0 or holding_hours < 8:
        return False, ""

    # Funding periods elapsed
    periods = holding_hours / 8.0
    # Cumulative funding cost as % of notional
    cumulative_funding_pct = abs(funding_rate_8h) * periods * leverage * 100

    # If position is profitable, check if funding is eating too much
    if current_r_multiple > 0:
        # Rough: 1R ~ 1-3% depending on SL. Use 2% as midpoint.
        approx_pnl_pct = current_r_multiple * 2.0
        funding_ratio = cumulative_funding_pct / approx_pnl_pct if approx_pnl_pct > 0 else 999

        if funding_ratio > 0.5:
            return True, (
                f"Funding drag warning: {cumulative_funding_pct:.2f}% cost "
                f"vs {approx_pnl_pct:.1f}% profit ({funding_ratio:.0%} of gains). "
                f"Rate: {funding_rate_8h:.4%}/8h, {periods:.1f} periods, {leverage}x leverage"
            )
    elif current_r_multiple <= 0 and cumulative_funding_pct > 0.5:
        # Underwater AND paying funding — strong exit signal
        return True, (
            f"Funding + underwater: {cumulative_funding_pct:.2f}% funding cost "
            f"on losing position (R={current_r_multiple:.2f}). "
            f"Rate: {funding_rate_8h:.4%}/8h, {periods:.1f} periods"
        )

    return False, ""


# ═══════════════════════════════════════════════════════════════════
# Signal-Type Hold Limits
# ═══════════════════════════════════════════════════════════════════

# Signal-type-specific hold time limits (hours)
_SIGNAL_HOLD_LIMITS = {
    "momentum_confluence": {"min_hours": 2.0, "max_hours": 8.0, "warn_hours": 6.0},
    "vwap_reversion": {"min_hours": 0.5, "max_hours": 2.0, "warn_hours": 1.5},
    "regime_trend": {"min_hours": 24.0, "max_hours": 72.0, "warn_hours": 48.0},
    "volume_spike": {"min_hours": 0.25, "max_hours": 1.5, "warn_hours": 1.0},
    "funding_arb": {"min_hours": 8.0, "max_hours": 48.0, "warn_hours": 24.0},
}


def check_signal_hold_limit(
    signal_type: str,
    holding_hours: float,
    current_r_multiple: float,
) -> tuple[bool, str]:
    """Check if a trade has exceeded its signal-type hold limit.

    Different signal types have different time-value decay profiles.
    A volume spike signal held for 4 hours is stale regardless of
    whether the strategy_type says "intraday" with a 4h limit.

    Returns:
        (should_exit, reason)
    """
    limits = _SIGNAL_HOLD_LIMITS.get(signal_type)
    if not limits:
        return False, ""

    max_hours = limits["max_hours"]

    # If past max hold time and trade isn't profitable, exit
    if holding_hours >= max_hours and current_r_multiple < 1.0:
        return True, (
            f"Signal hold limit: {signal_type} max {max_hours}h, "
            f"held {holding_hours:.1f}h with R={current_r_multiple:.2f}"
        )

    # Extended grace: if > 2x max hold but profitable, still flag it
    if holding_hours >= max_hours * 2:
        return True, (
            f"Signal hold hard limit: {signal_type} held {holding_hours:.1f}h "
            f"(2x max {max_hours}h), R={current_r_multiple:.2f}"
        )

    return False, ""


def check_vwap_reversion_exit(
    signal_type: str,
    current_price: float,
    vwap: float,
    direction: str,
) -> tuple[bool, str]:
    """For VWAP reversion trades, check if the thesis is complete or invalidated.

    Exit conditions:
    - Price recaptured VWAP from the other side (thesis complete)
    - Price breached VWAP by >0.3% on the wrong side (thesis invalid)
    """
    if signal_type != "vwap_reversion" or vwap <= 0:
        return False, ""

    dist_pct = (current_price - vwap) / vwap * 100

    if direction == "LONG":
        # LONG near VWAP: target is price moving above VWAP
        if dist_pct > 0.3:
            return True, f"VWAP reversion complete: price {dist_pct:+.2f}% above VWAP (target reached)"
        elif dist_pct < -0.3:
            return True, f"VWAP reversion failed: price {dist_pct:+.2f}% below VWAP (invalidated)"
    else:
        # SHORT near VWAP: target is price moving below VWAP
        if dist_pct < -0.3:
            return True, f"VWAP reversion complete: price {dist_pct:+.2f}% below VWAP (target reached)"
        elif dist_pct > 0.3:
            return True, f"VWAP reversion failed: price {dist_pct:+.2f}% above VWAP (invalidated)"

    return False, ""


class HoldTimeAnalytics:
    """Track hold duration vs outcome for signal quality diagnostics.

    Collects hold-time distribution for wins vs losses to identify:
    - Whether momentum signals are exited too early (leaving R on table)
    - Whether swing signals are held too long (giving back gains)
    - Optimal hold time per strategy type
    """

    def __init__(self) -> None:
        # strategy_type -> list of (holding_hours, r_multiple, is_win)
        self._records: dict[str, list[tuple[float, float, bool]]] = defaultdict(list)
        self._max_records = 500

    def record(self, strategy_type: str, holding_hours: float,
               r_multiple: float, is_win: bool) -> None:
        """Record a closed trade's hold time and outcome."""
        self._records[strategy_type].append((holding_hours, r_multiple, is_win))
        if len(self._records[strategy_type]) > self._max_records:
            self._records[strategy_type] = self._records[strategy_type][-self._max_records:]

    def get_analysis(self, strategy_type: str) -> Optional[dict]:
        """Analyze hold-time distribution for a strategy type.

        Returns dict with:
        - avg_hold_win: average hold time for winners
        - avg_hold_loss: average hold time for losers
        - avg_r_win: average R on winners
        - avg_r_loss: average R on losers
        - optimal_hold_range: suggested hold time range
        - recommendation: text advice
        """
        records = self._records.get(strategy_type, [])
        if len(records) < 10:
            return None

        wins = [(h, r) for h, r, w in records if w]
        losses = [(h, r) for h, r, w in records if not w]

        if not wins or not losses:
            return None

        avg_hold_win = sum(h for h, r in wins) / len(wins)
        avg_hold_loss = sum(h for h, r in losses) / len(losses)
        avg_r_win = sum(r for h, r in wins) / len(wins)
        avg_r_loss = sum(r for h, r in losses) / len(losses)

        # Optimal range: 80th percentile of winners
        sorted_win_hours = sorted(h for h, r in wins)
        p20 = sorted_win_hours[max(0, int(len(sorted_win_hours) * 0.2))]
        p80 = sorted_win_hours[min(len(sorted_win_hours) - 1, int(len(sorted_win_hours) * 0.8))]

        # Recommendation
        rec = ""
        if avg_hold_loss > avg_hold_win * 1.5:
            rec = "Losers held too long — tighten time exits"
        elif avg_hold_win < avg_hold_loss * 0.5:
            rec = "Winners cut too early — consider wider trailing"
        elif avg_r_win < 1.5:
            rec = "Average win is small — hold winners longer or widen TP"
        else:
            rec = "Hold-time distribution looks healthy"

        return {
            "total_trades": len(records),
            "win_rate": round(len(wins) / len(records) * 100, 1),
            "avg_hold_win_hours": round(avg_hold_win, 1),
            "avg_hold_loss_hours": round(avg_hold_loss, 1),
            "avg_r_win": round(avg_r_win, 2),
            "avg_r_loss": round(avg_r_loss, 2),
            "optimal_hold_range_hours": (round(p20, 1), round(p80, 1)),
            "recommendation": rec,
        }

    def summary(self) -> str:
        """Human-readable summary across all strategy types."""
        lines = ["HOLD-TIME ANALYTICS", ""]
        for st in ["scalp", "intraday", "swing", "position"]:
            analysis = self.get_analysis(st)
            if analysis is None:
                lines.append(f"{st.upper()}: insufficient data")
                continue
            lines.append(
                f"{st.upper()} ({analysis['total_trades']} trades, {analysis['win_rate']}% WR)\n"
                f"  Win hold: {analysis['avg_hold_win_hours']}h avg | "
                f"Loss hold: {analysis['avg_hold_loss_hours']}h avg\n"
                f"  Win R: {analysis['avg_r_win']} | Loss R: {analysis['avg_r_loss']}\n"
                f"  Optimal: {analysis['optimal_hold_range_hours'][0]}-"
                f"{analysis['optimal_hold_range_hours'][1]}h\n"
                f"  >> {analysis['recommendation']}"
            )
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 2. Volatility Squeeze Detector
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SqueezeSignal:
    """Bollinger Band squeeze detection result."""
    is_squeezing: bool          # currently in squeeze
    squeeze_bars: int           # how many bars in squeeze
    squeeze_fired: bool         # squeeze just released (breakout starting)
    fire_direction: str         # "bullish", "bearish", "unknown"
    bb_width_pct: float         # current BB width as % of price
    bb_width_percentile: float  # where current width sits in historical range
    momentum: float             # momentum direction at fire
    confidence: float
    description: str


def detect_squeeze(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    bb_period: int = 20,
    bb_std: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
    lookback: int = 100,
) -> Optional[SqueezeSignal]:
    """Detect Bollinger Band squeeze (BB inside Keltner Channel).

    A squeeze occurs when volatility contracts so much that the Bollinger Bands
    move inside the Keltner Channel. When the squeeze releases, a large move follows.
    """
    if len(closes) < max(bb_period, kc_period) + 10:
        return None

    c = closes.astype(float)
    h = highs.astype(float)
    l = lows.astype(float)

    # Bollinger Bands
    bb_ma = np.convolve(c, np.ones(bb_period)/bb_period, mode='valid')
    bb_ma = bb_ma[-min(len(bb_ma), lookback):]

    # Calculate rolling std
    bb_stds = []
    for i in range(bb_period - 1, len(c)):
        window = c[i - bb_period + 1:i + 1]
        bb_stds.append(float(np.std(window)))
    bb_stds = np.array(bb_stds[-len(bb_ma):])

    bb_upper = bb_ma + bb_std * bb_stds
    bb_lower = bb_ma - bb_std * bb_stds
    bb_width = (bb_upper - bb_lower) / bb_ma * 100  # as % of price

    # Keltner Channel (using ATR)
    tr = np.maximum(h[1:] - l[1:], np.maximum(
        np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])
    ))

    atr_vals = []
    if len(tr) >= kc_period:
        atr = float(np.mean(tr[:kc_period]))
        for i in range(kc_period, len(tr)):
            atr = (atr * (kc_period - 1) + float(tr[i])) / kc_period
            atr_vals.append(atr)

    if not atr_vals or len(bb_width) < 5:
        return None

    # Check squeeze: BB width relative to historical
    recent_width = float(bb_width[-1])
    hist_widths = bb_width[-min(len(bb_width), lookback):]
    width_percentile = float(np.searchsorted(np.sort(hist_widths), recent_width) / len(hist_widths) * 100)

    # Squeeze = BB width in bottom 20th percentile
    is_squeezing = width_percentile < 20

    # Count squeeze bars
    squeeze_bars = 0
    for i in range(len(bb_width) - 1, -1, -1):
        w_pctl = float(np.searchsorted(np.sort(hist_widths), bb_width[i]) / len(hist_widths) * 100)
        if w_pctl < 20:
            squeeze_bars += 1
        else:
            break

    # Check if squeeze just fired (was squeezing, now expanding)
    squeeze_fired = False
    fire_direction = "unknown"

    if squeeze_bars == 0 and len(bb_width) >= 3:
        prev_width = float(bb_width[-2])
        prev_pctl = float(np.searchsorted(np.sort(hist_widths), prev_width) / len(hist_widths) * 100)
        if prev_pctl < 25 and width_percentile >= 25:
            squeeze_fired = True
            # Direction: use momentum (close vs MA)
            if len(bb_ma) > 0 and float(c[-1]) > float(bb_ma[-1]):
                fire_direction = "bullish"
            else:
                fire_direction = "bearish"

    # Momentum using rate of change
    momentum = 0.0
    if len(c) >= 5:
        momentum = (float(c[-1]) - float(c[-5])) / float(c[-5]) * 100 if float(c[-5]) > 0 else 0

    # Confidence
    conf = 0.40
    if is_squeezing:
        conf += min(0.20, squeeze_bars * 0.02)  # longer squeeze = bigger move
    if squeeze_fired:
        conf += 0.25
    if width_percentile < 10:
        conf += 0.10  # extreme squeeze
    conf = min(0.90, conf)

    desc_parts = []
    if squeeze_fired:
        desc_parts.append(f"SQUEEZE FIRED {fire_direction.upper()}")
    elif is_squeezing:
        desc_parts.append(f"Squeezing ({squeeze_bars} bars)")
    else:
        desc_parts.append("No squeeze")
    desc_parts.append(f"BB width {recent_width:.2f}% (P{width_percentile:.0f})")

    return SqueezeSignal(
        is_squeezing=is_squeezing,
        squeeze_bars=squeeze_bars,
        squeeze_fired=squeeze_fired,
        fire_direction=fire_direction,
        bb_width_pct=round(recent_width, 3),
        bb_width_percentile=round(width_percentile, 1),
        momentum=round(momentum, 3),
        confidence=round(conf, 3),
        description=" | ".join(desc_parts),
    )

# ═══════════════════════════════════════════════════════════════════
# 3. Adaptive Limit Distance
# ═══════════════════════════════════════════════════════════════════

class AdaptiveLimitDistance:
    """Learns optimal limit order offset per symbol from fill history."""

    def __init__(self, state_file: str = "data/limit_distance_state.json") -> None:
        # symbol -> list of (offset_pct, filled: bool)
        self._history: dict[str, list[tuple[float, bool]]] = defaultdict(list)
        self._state_file = state_file
        self._max_per_symbol = 200
        self._load()

    def record(self, symbol: str, offset_pct: float, filled: bool) -> None:
        """Record whether a limit order at a given offset was filled."""
        self._history[symbol].append((offset_pct, filled))
        if len(self._history[symbol]) > self._max_per_symbol:
            self._history[symbol] = self._history[symbol][-self._max_per_symbol:]

        if sum(len(v) for v in self._history.values()) % 20 == 0:
            self._save()

    def optimal_offset(self, symbol: str, target_fill_rate: float = 0.70) -> float:
        """Calculate optimal limit offset for a target fill rate.

        Args:
            symbol: trading pair
            target_fill_rate: desired fill probability (0.70 = 70%)

        Returns:
            Optimal offset as % of price. Default 0.15% if no history.
        """
        records = self._history.get(symbol)
        if not records or len(records) < 5:
            return 0.15  # default 0.15%

        # Sort by offset
        sorted_records = sorted(records, key=lambda x: x[0])

        # Find the offset where fill rate crosses the target
        # Wider offset = higher fill rate (more likely to be hit)
        # We want the tightest offset that still achieves target fill rate

        offsets = [r[0] for r in sorted_records]
        unique_offsets = sorted(set(offsets))

        for offset in unique_offsets:
            fills_at_or_wider = sum(1 for o, f in sorted_records if o >= offset and f)
            total_at_or_wider = sum(1 for o, f in sorted_records if o >= offset)

            if total_at_or_wider > 0:
                fill_rate = fills_at_or_wider / total_at_or_wider
                if fill_rate >= target_fill_rate:
                    return round(offset, 4)

        # If no offset achieves target, use the median filled offset
        filled_offsets = [o for o, f in sorted_records if f]
        if filled_offsets:
            return round(float(np.median(filled_offsets)), 4)

        return 0.15

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._state_file) or ".", exist_ok=True)
            data = {sym: records[-100:] for sym, records in self._history.items()}
            with open(self._state_file, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if not os.path.exists(self._state_file):
                return
            with open(self._state_file) as f:
                data = json.load(f)
            for sym, records in data.items():
                self._history[sym] = [(r[0], r[1]) for r in records]
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════
# 4. Time-of-Day Edge Filter
# ═══════════════════════════════════════════════════════════════════

class TimeOfDayEdge:
    """Tracks win rate by hour-of-day per symbol."""

    def __init__(self) -> None:
        # symbol -> hour -> {"wins": int, "total": int}
        self._stats: dict[str, dict[int, dict]] = defaultdict(
            lambda: defaultdict(lambda: {"wins": 0, "total": 0})
        )

    def record(self, symbol: str, hour_utc: int, is_win: bool) -> None:
        """Record a trade outcome at a specific hour."""
        self._stats[symbol][hour_utc]["total"] += 1
        if is_win:
            self._stats[symbol][hour_utc]["wins"] += 1

    def get_edge(self, symbol: str, hour_utc: int) -> tuple[float, float]:
        """Get confidence adjustment for trading this symbol at this hour.

        Returns:
            (confidence_adj, description_wr)
            confidence_adj: -0.05 to +0.05
        """
        stats = self._stats.get(symbol, {}).get(hour_utc)
        if not stats or stats["total"] < 5:
            return 0.0, 0.0  # not enough data

        wr = stats["wins"] / stats["total"]

        if wr >= 0.70:
            return 0.05, wr   # strong edge at this hour
        elif wr >= 0.55:
            return 0.02, wr   # mild edge
        elif wr <= 0.30:
            return -0.05, wr  # avoid this hour
        elif wr <= 0.40:
            return -0.02, wr

        return 0.0, wr

    def get_best_hours(self, symbol: str, min_trades: int = 5) -> list[tuple[int, float]]:
        """Get hours ranked by win rate for a symbol."""
        results = []
        for hour, stats in self._stats.get(symbol, {}).items():
            if stats["total"] >= min_trades:
                wr = stats["wins"] / stats["total"]
                results.append((hour, wr))
        results.sort(key=lambda x: -x[1])
        return results

# ═══════════════════════════════════════════════════════════════════
# 5. Risk-Parity Sizing
# ═══════════════════════════════════════════════════════════════════

def risk_parity_size(
    equity: float,
    risk_per_trade_pct: float,
    stop_distance_pct: float,
    leverage: int = 1,
) -> float:
    """Calculate position size using risk-parity principle.

    Each position should contribute equal risk to the portfolio,
    regardless of the asset's volatility.

    Formula: size = (equity * risk_pct) / stop_distance_pct

    Args:
        equity: total account equity
        risk_per_trade_pct: max % of equity to risk (e.g., 1.0 = 1%)
        stop_distance_pct: SL distance as % of entry price
        leverage: leverage multiplier

    Returns:
        Position size in USD (notional).
    """
    if stop_distance_pct <= 0 or equity <= 0:
        return 0.0

    risk_amount = equity * (risk_per_trade_pct / 100.0)
    notional = risk_amount / (stop_distance_pct / 100.0)

    # With leverage, the margin (collateral) is notional / leverage
    # But we return notional — the caller divides by leverage for margin
    return round(notional, 2)
