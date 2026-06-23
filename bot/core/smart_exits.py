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
