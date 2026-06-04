"""
RUNECLAW Chart Pattern Detection — geometric price patterns.

Detects classic chart patterns from OHLCV data using swing point analysis:
  - Head & Shoulders / Inverse Head & Shoulders
  - Double Top / Double Bottom
  - Bull Flag / Bear Flag
  - Ascending / Descending / Symmetrical Triangle
  - Rising / Falling Wedge
  - Rectangle (Range)
  - Cup and Handle
  - Support/Resistance Flip
  - Basic Elliott Wave impulse counting

Design rules:
  - Fail-closed: insufficient data → empty results
  - Pure computation, no side effects
  - Returns structured pattern dicts with name, signal, confidence, description
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from bot.core.multi_timeframe import _find_swings


# ── Types ────────────────────────────────────────────────────────

PatternResult = dict  # {name, signal, confidence, description, key_levels}


# ── Helpers ──────────────────────────────────────────────────────

def _pct_diff(a: float, b: float) -> float:
    """Percentage difference between two values."""
    if a == 0:
        return 0.0
    return abs(a - b) / abs(a) * 100


def _trendline_slope(points: list[tuple[int, float]]) -> float:
    """Simple linear regression slope from (index, price) points."""
    if len(points) < 2:
        return 0.0
    x = np.array([p[0] for p in points], dtype=float)
    y = np.array([p[1] for p in points], dtype=float)
    n = len(x)
    slope = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / \
            (n * np.sum(x**2) - np.sum(x)**2 + 1e-10)
    return float(slope)


def _normalize_slope(slope: float, price: float) -> float:
    """Normalize slope as percentage of price per bar."""
    if price == 0:
        return 0.0
    return slope / price * 100


# ── Head & Shoulders ────────────────────────────────────────────

def detect_head_and_shoulders(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Head & Shoulders (bearish) or Inverse H&S (bullish).

    H&S: three swing highs where middle is highest (head), flanked by
    two lower and roughly equal shoulders.
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    # Need at least 3 swing highs for H&S
    if len(sh) >= 3:
        left, head, right = sh[-3], sh[-2], sh[-1]
        # Head must be highest
        if head[1] > left[1] and head[1] > right[1]:
            # Shoulders roughly equal (within 3%)
            shoulder_diff = _pct_diff(left[1], right[1])
            if shoulder_diff < 5.0:
                # Neckline from swing lows between shoulders
                neckline_lows = [s for s in sl if left[0] < s[0] < right[0]]
                neckline = np.mean([s[1] for s in neckline_lows]) if neckline_lows else min(left[1], right[1])
                price = float(closes[-1])
                conf = min(0.85, 0.6 + (1.0 - shoulder_diff / 5.0) * 0.25)
                if price < neckline:
                    conf = min(0.95, conf + 0.10)  # confirmed break
                return {
                    "name": "Head & Shoulders",
                    "signal": "bearish",
                    "confidence": round(conf, 2),
                    "description": f"H&S top: head ${head[1]:,.2f}, neckline ~${neckline:,.2f}",
                    "key_levels": {"head": head[1], "left_shoulder": left[1],
                                   "right_shoulder": right[1], "neckline": float(neckline)},
                }

    # Inverse H&S: three swing lows where middle is lowest
    if len(sl) >= 3:
        left, head, right = sl[-3], sl[-2], sl[-1]
        if head[1] < left[1] and head[1] < right[1]:
            shoulder_diff = _pct_diff(left[1], right[1])
            if shoulder_diff < 5.0:
                neckline_highs = [s for s in sh if left[0] < s[0] < right[0]]
                neckline = np.mean([s[1] for s in neckline_highs]) if neckline_highs else max(left[1], right[1])
                price = float(closes[-1])
                conf = min(0.85, 0.6 + (1.0 - shoulder_diff / 5.0) * 0.25)
                if price > neckline:
                    conf = min(0.95, conf + 0.10)
                return {
                    "name": "Inverse Head & Shoulders",
                    "signal": "bullish",
                    "confidence": round(conf, 2),
                    "description": f"IH&S bottom: head ${head[1]:,.2f}, neckline ~${neckline:,.2f}",
                    "key_levels": {"head": head[1], "left_shoulder": left[1],
                                   "right_shoulder": right[1], "neckline": float(neckline)},
                }

    return None


# ── Double Top / Bottom ─────────────────────────────────────────

def detect_double_top_bottom(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Double Top (bearish) or Double Bottom (bullish)."""
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    # Double Top: two swing highs at roughly same level
    if len(sh) >= 2:
        top1, top2 = sh[-2], sh[-1]
        diff = _pct_diff(top1[1], top2[1])
        if diff < 3.0:
            price = float(closes[-1])
            # Trough between tops
            trough_lows = [s for s in sl if top1[0] < s[0] < top2[0]]
            neckline = min(s[1] for s in trough_lows) if trough_lows else min(top1[1], top2[1]) * 0.97
            conf = min(0.85, 0.55 + (1.0 - diff / 3.0) * 0.30)
            if price < neckline:
                conf = min(0.90, conf + 0.10)
            return {
                "name": "Double Top",
                "signal": "bearish",
                "confidence": round(conf, 2),
                "description": f"Double top at ~${top1[1]:,.2f}, neckline ~${neckline:,.2f}",
                "key_levels": {"top1": top1[1], "top2": top2[1], "neckline": float(neckline)},
            }

    # Double Bottom: two swing lows at roughly same level
    if len(sl) >= 2:
        bot1, bot2 = sl[-2], sl[-1]
        diff = _pct_diff(bot1[1], bot2[1])
        if diff < 3.0:
            price = float(closes[-1])
            peak_highs = [s for s in sh if bot1[0] < s[0] < bot2[0]]
            neckline = max(s[1] for s in peak_highs) if peak_highs else max(bot1[1], bot2[1]) * 1.03
            conf = min(0.85, 0.55 + (1.0 - diff / 3.0) * 0.30)
            if price > neckline:
                conf = min(0.90, conf + 0.10)
            return {
                "name": "Double Bottom",
                "signal": "bullish",
                "confidence": round(conf, 2),
                "description": f"Double bottom at ~${bot1[1]:,.2f}, neckline ~${neckline:,.2f}",
                "key_levels": {"bot1": bot1[1], "bot2": bot2[1], "neckline": float(neckline)},
            }

    return None


# ── Flags ────────────────────────────────────────────────────────

def detect_flags(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Bull Flag or Bear Flag.

    Flag = strong impulsive move followed by a gentle counter-trend
    consolidation (the flag pole + flag).
    """
    if len(closes) < 30:
        return None

    # Check last 30 bars: split into pole (first 10) and flag (last 20)
    pole_closes = closes[-30:-20]
    flag_closes = closes[-20:]
    flag_highs = highs[-20:]
    flag_lows = lows[-20:]

    pole_move = float(pole_closes[-1] - pole_closes[0])
    pole_pct = abs(pole_move) / float(pole_closes[0]) * 100 if pole_closes[0] != 0 else 0

    if pole_pct < 3.0:  # Need at least 3% impulse move
        return None

    # Flag: counter-trend slope should be gentle and opposite to pole
    flag_points = [(i, float(flag_closes[i])) for i in range(len(flag_closes))]
    flag_slope = _trendline_slope(flag_points)
    norm_slope = _normalize_slope(flag_slope, float(closes[-1]))

    # Flag range should be tighter than pole
    flag_range = float(np.max(flag_highs) - np.min(flag_lows))
    pole_range = abs(pole_move)

    if flag_range > pole_range * 0.5:
        return None  # flag too wide

    if pole_move > 0 and norm_slope < 0 and abs(norm_slope) < 0.3:
        # Bull flag: pole up, flag slopes gently down
        conf = min(0.80, 0.50 + pole_pct / 20)
        return {
            "name": "Bull Flag",
            "signal": "bullish",
            "confidence": round(conf, 2),
            "description": f"Bull flag: {pole_pct:.1f}% pole, consolidating",
            "key_levels": {"pole_base": float(pole_closes[0]),
                           "pole_top": float(pole_closes[-1]),
                           "flag_low": float(np.min(flag_lows))},
        }

    if pole_move < 0 and norm_slope > 0 and abs(norm_slope) < 0.3:
        # Bear flag: pole down, flag slopes gently up
        conf = min(0.80, 0.50 + pole_pct / 20)
        return {
            "name": "Bear Flag",
            "signal": "bearish",
            "confidence": round(conf, 2),
            "description": f"Bear flag: {pole_pct:.1f}% drop, consolidating",
            "key_levels": {"pole_top": float(pole_closes[0]),
                           "pole_base": float(pole_closes[-1]),
                           "flag_high": float(np.max(flag_highs))},
        }

    return None


# ── Triangles ────────────────────────────────────────────────────

def detect_triangles(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Ascending, Descending, or Symmetrical Triangle."""
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    if len(sh) < 2 or len(sl) < 2:
        return None

    high_slope = _trendline_slope(sh[-3:] if len(sh) >= 3 else sh[-2:])
    low_slope = _trendline_slope(sl[-3:] if len(sl) >= 3 else sl[-2:])

    price = float(closes[-1])
    nh = _normalize_slope(high_slope, price)
    nl = _normalize_slope(low_slope, price)

    # Ascending: flat highs, rising lows
    if abs(nh) < 0.05 and nl > 0.02:
        return {
            "name": "Ascending Triangle",
            "signal": "bullish",
            "confidence": 0.70,
            "description": f"Ascending triangle: flat resistance ~${sh[-1][1]:,.2f}, rising lows",
            "key_levels": {"resistance": sh[-1][1], "support_rising": sl[-1][1]},
        }

    # Descending: falling highs, flat lows
    if nh < -0.02 and abs(nl) < 0.05:
        return {
            "name": "Descending Triangle",
            "signal": "bearish",
            "confidence": 0.70,
            "description": f"Descending triangle: falling highs, flat support ~${sl[-1][1]:,.2f}",
            "key_levels": {"resistance_falling": sh[-1][1], "support": sl[-1][1]},
        }

    # Symmetrical: converging — highs falling AND lows rising
    if nh < -0.02 and nl > 0.02:
        return {
            "name": "Symmetrical Triangle",
            "signal": "neutral",
            "confidence": 0.60,
            "description": "Symmetrical triangle: converging trendlines, breakout imminent",
            "key_levels": {"upper": sh[-1][1], "lower": sl[-1][1]},
        }

    return None


# ── Wedges ───────────────────────────────────────────────────────

def detect_wedges(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Rising Wedge (bearish) or Falling Wedge (bullish).

    Both trendlines slope in the same direction but converge.
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    if len(sh) < 2 or len(sl) < 2:
        return None

    high_slope = _trendline_slope(sh[-3:] if len(sh) >= 3 else sh[-2:])
    low_slope = _trendline_slope(sl[-3:] if len(sl) >= 3 else sl[-2:])

    price = float(closes[-1])
    nh = _normalize_slope(high_slope, price)
    nl = _normalize_slope(low_slope, price)

    # Rising wedge: both slopes positive, but highs slope < lows slope (converging)
    if nh > 0.01 and nl > 0.01 and nl > nh:
        return {
            "name": "Rising Wedge",
            "signal": "bearish",
            "confidence": 0.65,
            "description": "Rising wedge: converging upward trendlines, bearish reversal pattern",
            "key_levels": {"upper": sh[-1][1], "lower": sl[-1][1]},
        }

    # Falling wedge: both slopes negative, but lows slope < highs slope (converging)
    if nh < -0.01 and nl < -0.01 and nh > nl:
        return {
            "name": "Falling Wedge",
            "signal": "bullish",
            "confidence": 0.65,
            "description": "Falling wedge: converging downward trendlines, bullish reversal pattern",
            "key_levels": {"upper": sh[-1][1], "lower": sl[-1][1]},
        }

    return None


# ── Rectangle / Range ────────────────────────────────────────────

def detect_rectangle(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Rectangle / Range-bound price action."""
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    if len(sh) < 2 or len(sl) < 2:
        return None

    # Check if swing highs are at roughly same level AND swing lows are too
    high_vals = [s[1] for s in sh[-3:]]
    low_vals = [s[1] for s in sl[-3:]]

    high_range_pct = (max(high_vals) - min(high_vals)) / max(high_vals) * 100 if max(high_vals) > 0 else 999
    low_range_pct = (max(low_vals) - min(low_vals)) / max(low_vals) * 100 if max(low_vals) > 0 else 999

    if high_range_pct < 2.0 and low_range_pct < 2.0:
        resistance = np.mean(high_vals)
        support = np.mean(low_vals)
        price = float(closes[-1])
        # Determine bias from where price sits in range
        mid = (resistance + support) / 2
        signal = "bullish" if price < mid else "bearish" if price > mid else "neutral"
        return {
            "name": "Rectangle",
            "signal": signal,
            "confidence": 0.65,
            "description": f"Range: ${support:,.2f} - ${resistance:,.2f}",
            "key_levels": {"support": float(support), "resistance": float(resistance)},
        }

    return None


# ── Cup and Handle ───────────────────────────────────────────────

def detect_cup_and_handle(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Cup and Handle pattern (bullish continuation).

    Cup: U-shaped bottom with roughly equal highs on both sides.
    Handle: small pullback after the right lip of the cup.
    """
    if len(closes) < 40:
        return None

    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    if len(sh) < 3 or len(sl) < 2:
        return None

    # Look for a deep swing low flanked by two higher swing highs (the lips)
    # Use the last 3 swing highs and find the deepest swing low between the first two
    left_lip = sh[-3]
    right_lip = sh[-2]
    handle_high = sh[-1]

    # Cup bottom: deepest swing low between left and right lip
    cup_lows = [s for s in sl if left_lip[0] < s[0] < right_lip[0]]
    if not cup_lows:
        return None

    cup_bottom = min(cup_lows, key=lambda s: s[1])

    # Lips should be roughly equal (within 3%)
    lip_diff = _pct_diff(left_lip[1], right_lip[1])
    if lip_diff > 3.0:
        return None

    # Cup depth should be meaningful (at least 5% from lip)
    avg_lip = (left_lip[1] + right_lip[1]) / 2
    cup_depth_pct = (avg_lip - cup_bottom[1]) / avg_lip * 100
    if cup_depth_pct < 5.0:
        return None

    # Handle: the last swing high should be slightly below right lip
    if handle_high[0] > right_lip[0] and handle_high[1] <= right_lip[1]:
        price = float(closes[-1])
        conf = min(0.80, 0.55 + cup_depth_pct / 40)
        return {
            "name": "Cup and Handle",
            "signal": "bullish",
            "confidence": round(conf, 2),
            "description": f"Cup & handle: depth {cup_depth_pct:.1f}%, breakout ~${right_lip[1]:,.2f}",
            "key_levels": {"left_lip": left_lip[1], "right_lip": right_lip[1],
                           "cup_bottom": cup_bottom[1], "breakout": right_lip[1]},
        }

    return None


# ── S/R Flip Detection ──────────────────────────────────────────

def detect_sr_flip(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect Support becoming Resistance or Resistance becoming Support.

    Looks for a price level that was tested as support, broken, then retested as resistance
    (or vice versa).
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    price = float(closes[-1])

    # S→R Flip: old support (swing low) that price broke below, now acting as resistance
    if len(sl) >= 2 and len(sh) >= 1:
        old_support = sl[-2][1]
        # Price broke below old support at some point
        broke_below = any(float(lows[i]) < old_support * 0.995 for i in range(sl[-2][0], len(lows)))
        # Now price is approaching from below, last swing high near old support
        if broke_below and sh[-1][0] > sl[-2][0]:
            near_level = _pct_diff(sh[-1][1], old_support) < 1.5
            if near_level and price < old_support:
                return {
                    "name": "S/R Flip (Support → Resistance)",
                    "signal": "bearish",
                    "confidence": 0.70,
                    "description": f"Old support ${old_support:,.2f} now acting as resistance",
                    "key_levels": {"level": old_support},
                }

    # R→S Flip: old resistance (swing high) that price broke above, now acting as support
    if len(sh) >= 2 and len(sl) >= 1:
        old_resistance = sh[-2][1]
        broke_above = any(float(highs[i]) > old_resistance * 1.005 for i in range(sh[-2][0], len(highs)))
        if broke_above and sl[-1][0] > sh[-2][0]:
            near_level = _pct_diff(sl[-1][1], old_resistance) < 1.5
            if near_level and price > old_resistance:
                return {
                    "name": "S/R Flip (Resistance → Support)",
                    "signal": "bullish",
                    "confidence": 0.70,
                    "description": f"Old resistance ${old_resistance:,.2f} now acting as support",
                    "key_levels": {"level": old_resistance},
                }

    return None


# ── Elliott Wave (Basic) ─────────────────────────────────────────

def detect_elliott_impulse(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Basic Elliott Wave 5-wave impulse detection.

    Rules:
      - Wave 2 cannot retrace beyond the start of wave 1
      - Wave 3 cannot be the shortest wave
      - Wave 4 cannot overlap wave 1 territory

    This is a simplified heuristic — true Elliott counting is subjective.
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    # Need alternating swing points to form 5 waves
    # Bullish impulse: low → high → low → high → low → high (5 up waves)
    if len(sh) >= 3 and len(sl) >= 2:
        # Try bullish: start from a swing low, alternate up-down
        # Wave structure: SL0 → SH0 → SL1 → SH1 → SL2 → SH2
        if (sl[0][0] < sh[0][0] < sl[1][0] < sh[1][0]):
            w1_start = sl[0][1]  # wave 1 bottom
            w1_end = sh[0][1]    # wave 1 top
            w2_end = sl[1][1]    # wave 2 bottom
            w3_end = sh[1][1]    # wave 3 top

            # Optionally wave 4 & 5
            if len(sl) >= 3 and len(sh) >= 3 and sl[2][0] > sh[1][0]:
                w4_end = sl[2][1]
                w5_end = sh[2][1]
            else:
                w4_end = None
                w5_end = None

            # Rule 1: Wave 2 does not retrace below wave 1 start
            if w2_end < w1_start:
                return None

            w1_len = w1_end - w1_start
            w3_len = w3_end - w2_end

            # Rule 2: Wave 3 is not the shortest
            if w4_end is not None and w5_end is not None:
                w5_len = w5_end - w4_end
                if w3_len < w1_len and w3_len < w5_len:
                    return None
                # Rule 3: Wave 4 does not overlap wave 1 territory
                if w4_end < w1_end:
                    return None

                conf = 0.65
                current_wave = "5" if float(closes[-1]) > w4_end else "4"
                return {
                    "name": "Elliott 5-Wave Impulse",
                    "signal": "bullish",
                    "confidence": conf,
                    "description": f"Bullish impulse: currently in wave {current_wave}",
                    "key_levels": {"w1_start": w1_start, "w3_top": w3_end,
                                   "w4_low": w4_end, "w5_top": w5_end},
                }
            else:
                # Partial count — waves 1-3 visible
                if w3_len > w1_len:  # wave 3 extending — classic
                    return {
                        "name": "Elliott Impulse (Partial)",
                        "signal": "bullish",
                        "confidence": 0.50,
                        "description": "Bullish impulse forming: waves 1-3 visible, wave 3 extending",
                        "key_levels": {"w1_start": w1_start, "w1_top": w1_end,
                                       "w2_low": w2_end, "w3_top": w3_end},
                    }

    # Bearish impulse: mirror logic
    if len(sl) >= 3 and len(sh) >= 2:
        if (sh[0][0] < sl[0][0] < sh[1][0] < sl[1][0]):
            w1_start = sh[0][1]
            w1_end = sl[0][1]
            w2_end = sh[1][1]
            w3_end = sl[1][1]

            if w2_end > w1_start:
                return None

            w1_len = w1_start - w1_end
            w3_len = w2_end - w3_end

            if w3_len > w1_len:
                return {
                    "name": "Elliott Impulse (Bearish)",
                    "signal": "bearish",
                    "confidence": 0.50,
                    "description": "Bearish impulse forming: waves 1-3 visible",
                    "key_levels": {"w1_start": w1_start, "w1_low": w1_end,
                                   "w2_high": w2_end, "w3_low": w3_end},
                }

    return None


# ── Liquidity Sweep ──────────────────────────────────────────────

def detect_liquidity_sweep(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> Optional[PatternResult]:
    """Detect liquidity sweep: price briefly pierces a swing level then reverses.

    Bullish sweep: wick below a swing low, close back above → trapped sellers.
    Bearish sweep: wick above a swing high, close back below → trapped buyers.
    """
    swings = _find_swings(highs, lows, lookback)
    sh = swings["swing_highs"]
    sl = swings["swing_lows"]

    price = float(closes[-1])

    # Check last 3 bars for sweeps (not just the last bar)
    check_bars = min(3, len(lows))

    # Bullish sweep: recent bar wick went below a prior swing low but price closed above it
    if sl:
        nearest_sl = sl[-1][1]
        for offset in range(1, check_bars + 1):
            last_low = float(lows[-offset])
            if last_low < nearest_sl * 0.998 and price > nearest_sl:
                return {
                    "name": "Liquidity Sweep (Bullish)",
                    "signal": "bullish",
                    "confidence": 0.70,
                    "description": f"Swept lows at ${nearest_sl:,.2f}, reclaimed — trapped sellers",
                    "key_levels": {"swept_level": nearest_sl, "wick_low": last_low},
                }

    # Bearish sweep: recent bar wick above a prior swing high but price closed below it
    if sh:
        nearest_sh = sh[-1][1]
        for offset in range(1, check_bars + 1):
            last_high = float(highs[-offset])
            if last_high > nearest_sh * 1.002 and price < nearest_sh:
                return {
                    "name": "Liquidity Sweep (Bearish)",
                    "signal": "bearish",
                    "confidence": 0.70,
                    "description": f"Swept highs at ${nearest_sh:,.2f}, rejected — trapped buyers",
                    "key_levels": {"swept_level": nearest_sh, "wick_high": last_high},
                }

    return None


# ── Master Scanner ───────────────────────────────────────────────

def scan_all_chart_patterns(
    opens: np.ndarray, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
    lookback: int = 5,
) -> list[PatternResult]:
    """Run all chart pattern detectors and return found patterns.

    Returns a list of PatternResult dicts, sorted by confidence descending.
    """
    if len(closes) < 20:
        return []

    # TODO: Optimization — compute swing points ONCE here and pass to each
    # detector instead of each detector calling _find_swings independently.
    # Requires adding an optional `swings` parameter to each detector function.
    # swings = _find_swings(highs, lows, lookback)

    detectors = [
        detect_head_and_shoulders,
        detect_double_top_bottom,
        detect_flags,
        detect_triangles,
        detect_wedges,
        detect_rectangle,
        detect_cup_and_handle,
        detect_sr_flip,
        detect_elliott_impulse,
        detect_liquidity_sweep,
    ]

    results: list[PatternResult] = []
    for detector in detectors:
        try:
            pattern = detector(highs, lows, closes, lookback)
            if pattern:
                results.append(pattern)
        except Exception:
            continue  # fail-closed: skip broken detector

    results.sort(key=lambda p: p.get("confidence", 0), reverse=True)
    return results
