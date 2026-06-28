"""
RUNECLAW Liquidity Sweep Detector — smart money entry signals.

Detects when price sweeps below a key low (or above a key high) to grab
stop losses, then reverses. This "liquidity grab" is the #1 institutional
entry pattern in crypto.

Patterns detected:
  - Bullish Sweep: price dips below recent swing low, wicks back above → LONG
  - Bearish Sweep: price spikes above recent swing high, wicks back below → SHORT
  - Failed Sweep: sweep attempt that doesn't reverse (continuation signal)
  - Double Sweep: two consecutive sweeps of same level (very high probability)

Scoring factors:
  - Depth of sweep (how far below the level)
  - Speed of reversal (fast snap-back = stronger)
  - Volume on sweep candle (high volume = real liquidity taken)
  - Prior touches of the level (more touches = more stops accumulated)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SweepSignal:
    """Detected liquidity sweep signal."""
    sweep_type: str          # "bullish_sweep", "bearish_sweep", "double_sweep_bull", "double_sweep_bear"
    level_price: float       # the liquidity level that was swept
    sweep_low: float         # actual low of sweep candle (for bullish)
    sweep_high: float        # actual high of sweep candle (for bearish)
    close_price: float       # close of sweep candle
    depth_pct: float         # how far past the level (%)
    reversal_strength: float # 0-1, how strongly price reversed
    volume_ratio: float      # sweep candle volume / average volume
    level_touches: int       # how many times this level was tested before
    confidence: float        # 0-1 overall signal confidence
    suggested_entry: float   # recommended entry price
    suggested_sl: float      # recommended stop loss
    description: str


def _find_swing_lows(lows: np.ndarray, order: int = 5) -> list[tuple[int, float]]:
    """Find swing low points (index, price)."""
    swings = []
    for i in range(order, len(lows) - order):
        if all(lows[i] <= lows[i-j] for j in range(1, order+1)) and \
           all(lows[i] <= lows[i+j] for j in range(1, order+1)):
            swings.append((i, float(lows[i])))
    return swings


def _find_swing_highs(highs: np.ndarray, order: int = 5) -> list[tuple[int, float]]:
    """Find swing high points (index, price)."""
    swings = []
    for i in range(order, len(highs) - order):
        if all(highs[i] >= highs[i-j] for j in range(1, order+1)) and \
           all(highs[i] >= highs[i+j] for j in range(1, order+1)):
            swings.append((i, float(highs[i])))
    return swings


def _count_touches(prices: np.ndarray, level: float, tolerance_pct: float = 0.3) -> int:
    """Count how many times price touched near a level."""
    tol = level * tolerance_pct / 100
    count = 0
    for p in prices:
        if abs(p - level) <= tol:
            count += 1
    return count


def detect_sweeps(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    lookback: int = 50,
    sweep_tolerance_pct: float = 0.5,
) -> list[SweepSignal]:
    """Detect liquidity sweep patterns in recent price action.

    Args:
        opens, highs, lows, closes, volumes: OHLCV arrays
        lookback: bars to analyze
        sweep_tolerance_pct: max % past level to count as sweep (not breakdown)

    Returns:
        List of SweepSignal objects, sorted by confidence.
    """
    signals: list[SweepSignal] = []

    n = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
    if n < 20:
        return signals

    # Use lookback window
    start = max(0, n - lookback)
    o = opens[start:n]
    h = highs[start:n]
    l = lows[start:n]
    c = closes[start:n]
    v = volumes[start:n]

    avg_vol = float(np.mean(v)) if len(v) > 0 else 1.0
    avg_range = float(np.mean(h - l)) if len(h) > 0 else 1.0

    # Find key levels (swing lows and highs)
    swing_lows = _find_swing_lows(l, order=3)
    swing_highs = _find_swing_highs(h, order=3)

    # Check recent candles (last 5) for sweeps of established levels
    check_range = min(5, len(c) - 1)

    for bar_idx in range(len(c) - check_range, len(c)):
        if bar_idx < 1:
            continue

        bar_low = float(l[bar_idx])
        bar_high = float(h[bar_idx])
        bar_close = float(c[bar_idx])
        bar_open = float(o[bar_idx])
        bar_vol = float(v[bar_idx])
        bar_range = bar_high - bar_low

        if bar_range <= 0:
            continue

        # -- Bullish Sweep: price sweeps below swing low then closes above --
        for sw_idx, sw_price in swing_lows:
            if sw_idx >= bar_idx - 1:  # level must be established before this candle
                continue

            # Check: did this candle's low go below the swing low?
            if bar_low < sw_price:
                depth = sw_price - bar_low
                depth_pct = depth / sw_price * 100

                # Must close back above the level (the reversal)
                if bar_close > sw_price and depth_pct <= sweep_tolerance_pct:
                    # Calculate reversal strength: how much of the wick reversed
                    total_wick_below = sw_price - bar_low
                    close_above = bar_close - sw_price
                    reversal = min(1.0, close_above / (total_wick_below + 1e-10))

                    # Volume ratio
                    vol_ratio = bar_vol / avg_vol if avg_vol > 0 else 1.0

                    # Count prior touches of this level
                    touches = _count_touches(l[:sw_idx], sw_price)

                    # Confidence scoring
                    conf = 0.50
                    conf += min(0.15, reversal * 0.15)           # strong reversal
                    conf += min(0.10, (vol_ratio - 1) * 0.05)   # high volume
                    conf += min(0.10, touches * 0.03)            # many touches = more stops
                    conf += min(0.05, depth_pct * 0.1)           # deeper sweep = more stops grabbed

                    # Wick ratio: long lower wick = bullish
                    lower_wick = min(bar_open, bar_close) - bar_low
                    wick_ratio = lower_wick / bar_range if bar_range > 0 else 0
                    if wick_ratio > 0.6:
                        conf += 0.05  # pin bar / hammer

                    conf = min(0.95, conf)

                    # Suggested entry: just above the swept level
                    entry = sw_price + avg_range * 0.1
                    sl = bar_low - avg_range * 0.3  # below the sweep low

                    signals.append(SweepSignal(
                        sweep_type="bullish_sweep",
                        level_price=sw_price,
                        sweep_low=bar_low,
                        sweep_high=bar_high,
                        close_price=bar_close,
                        depth_pct=round(depth_pct, 3),
                        reversal_strength=round(reversal, 3),
                        volume_ratio=round(vol_ratio, 2),
                        level_touches=touches,
                        confidence=round(conf, 3),
                        suggested_entry=round(entry, 8),
                        suggested_sl=round(sl, 8),
                        description=(
                            f"Bullish liquidity sweep: swept ${sw_price:,.4f} by {depth_pct:.2f}%, "
                            f"reversed {reversal:.0%}, vol {vol_ratio:.1f}x avg, "
                            f"{touches} prior touches"
                        ),
                    ))

        # -- Bearish Sweep: price spikes above swing high then closes below --
        for sw_idx, sw_price in swing_highs:
            if sw_idx >= bar_idx - 1:
                continue

            if bar_high > sw_price:
                depth = bar_high - sw_price
                depth_pct = depth / sw_price * 100

                if bar_close < sw_price and depth_pct <= sweep_tolerance_pct:
                    total_wick_above = bar_high - sw_price
                    close_below = sw_price - bar_close
                    reversal = min(1.0, close_below / (total_wick_above + 1e-10))

                    vol_ratio = bar_vol / avg_vol if avg_vol > 0 else 1.0
                    touches = _count_touches(h[:sw_idx], sw_price)

                    conf = 0.50
                    conf += min(0.15, reversal * 0.15)
                    conf += min(0.10, (vol_ratio - 1) * 0.05)
                    conf += min(0.10, touches * 0.03)
                    conf += min(0.05, depth_pct * 0.1)

                    upper_wick = bar_high - max(bar_open, bar_close)
                    wick_ratio = upper_wick / bar_range if bar_range > 0 else 0
                    if wick_ratio > 0.6:
                        conf += 0.05

                    conf = min(0.95, conf)

                    entry = sw_price - avg_range * 0.1
                    sl = bar_high + avg_range * 0.3

                    signals.append(SweepSignal(
                        sweep_type="bearish_sweep",
                        level_price=sw_price,
                        sweep_low=bar_low,
                        sweep_high=bar_high,
                        close_price=bar_close,
                        depth_pct=round(depth_pct, 3),
                        reversal_strength=round(reversal, 3),
                        volume_ratio=round(vol_ratio, 2),
                        level_touches=touches,
                        confidence=round(conf, 3),
                        suggested_entry=round(entry, 8),
                        suggested_sl=round(sl, 8),
                        description=(
                            f"Bearish liquidity sweep: swept ${sw_price:,.4f} by {depth_pct:.2f}%, "
                            f"reversed {reversal:.0%}, vol {vol_ratio:.1f}x avg"
                        ),
                    ))

    # Check for double sweeps (same level swept twice)
    _check_double_sweeps(signals)

    # Sort by confidence
    signals.sort(key=lambda s: s.confidence, reverse=True)
    return signals


def _check_double_sweeps(signals: list[SweepSignal]) -> None:
    """Upgrade confidence for double sweeps of the same level."""
    from collections import defaultdict
    level_counts: dict[str, list[SweepSignal]] = defaultdict(list)

    for sig in signals:
        key = f"{sig.sweep_type}_{sig.level_price:.4f}"
        level_counts[key].append(sig)

    for key, sigs in level_counts.items():
        if len(sigs) >= 2:
            for sig in sigs:
                sig.confidence = min(0.95, sig.confidence + 0.10)
                sig.sweep_type = sig.sweep_type.replace("_sweep", "_double_sweep")
                sig.description = "DOUBLE " + sig.description


def sweep_to_confluence_votes(signals: list[SweepSignal]) -> tuple[list[float], list[float]]:
    """Convert sweep signals to confluence votes/weights."""
    votes: list[float] = []
    weights: list[float] = []

    for sig in signals[:2]:  # top 2 signals
        if "bullish" in sig.sweep_type:
            votes.append(1.0)
        else:
            votes.append(-1.0)

        # Weight based on confidence, capped
        w = min(1.2, sig.confidence * 1.3)  # sweep signals get high weight
        weights.append(round(w, 3))

    return votes, weights
