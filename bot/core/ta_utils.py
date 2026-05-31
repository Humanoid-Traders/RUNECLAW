"""
RUNECLAW Technical Analysis Utilities -- shared functions used across modules.

Extracted to avoid circular imports between analyzer.py, multi_timeframe.py,
and other core modules that need EMA, ADX, and Regime.
"""

from __future__ import annotations

from enum import Enum

import numpy as np


class Regime(str, Enum):
    """Market regime classification based on ADX + directional movement."""
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    CHOP = "CHOP"
    UNKNOWN = "UNKNOWN"


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average over full array."""
    alpha = 2 / (period + 1)
    out = np.empty_like(data, dtype=float)
    out[0] = data[0]
    for i in range(1, len(data)):
        out[i] = alpha * data[i] + (1 - alpha) * out[i - 1]
    return out


def _compute_adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> dict:
    """
    Average Directional Index with +DI and -DI.
    Returns dict with 'adx', 'plus_di', 'minus_di'.
    """
    if len(highs) < period + 1:
        return {"adx": 0.0, "plus_di": 0.0, "minus_di": 0.0}

    # True Range
    tr_hl = highs[1:] - lows[1:]
    tr_hc = np.abs(highs[1:] - closes[:-1])
    tr_lc = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(tr_hl, np.maximum(tr_hc, tr_lc))

    # Directional Movement
    up_move = highs[1:] - highs[:-1]
    down_move = lows[:-1] - lows[1:]

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    # Wilder's smoothing
    atr = np.zeros(len(tr))
    atr[period - 1] = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    smoothed_plus = np.zeros(len(plus_dm))
    smoothed_plus[period - 1] = np.mean(plus_dm[:period])
    for i in range(period, len(plus_dm)):
        smoothed_plus[i] = (smoothed_plus[i - 1] * (period - 1) + plus_dm[i]) / period

    smoothed_minus = np.zeros(len(minus_dm))
    smoothed_minus[period - 1] = np.mean(minus_dm[:period])
    for i in range(period, len(minus_dm)):
        smoothed_minus[i] = (smoothed_minus[i - 1] * (period - 1) + minus_dm[i]) / period

    # +DI and -DI (safe division)
    # LB-1 FIX: Only compute DI from index period-1 onward where smoothed
    # values are valid. Early indices (0 to period-2) are zero from
    # initialization and would bias DX/ADX downward on short windows.
    with np.errstate(invalid="ignore", divide="ignore"):
        plus_di = np.where(atr > 0, 100 * smoothed_plus / atr, 0.0)
        minus_di = np.where(atr > 0, 100 * smoothed_minus / atr, 0.0)
        # Zero out invalid early indices to prevent them from polluting DX
        plus_di[:period - 1] = 0.0
        minus_di[:period - 1] = 0.0
        plus_di = np.nan_to_num(plus_di, nan=0.0)
        minus_di = np.nan_to_num(minus_di, nan=0.0)

    # DX and ADX — only from period-1 onward
    di_sum = plus_di + minus_di
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0.0)
        dx = np.nan_to_num(dx, nan=0.0)
        # Zero out pre-smoothing indices
        dx[:period - 1] = 0.0

    # ADX: Wilder smoothing of DX, starting from valid DX values only
    valid_dx = dx[period - 1:]  # only valid DX values
    if len(valid_dx) >= period:
        adx = np.zeros(len(valid_dx))
        adx[period - 1] = np.mean(valid_dx[:period])
        for i in range(period, len(valid_dx)):
            adx[i] = (adx[i - 1] * (period - 1) + valid_dx[i]) / period
        adx_val = float(adx[-1])
    else:
        adx_val = float(np.mean(valid_dx)) if len(valid_dx) > 0 else 0.0

    return {
        "adx": round(adx_val, 2),
        "plus_di": round(float(plus_di[-1]), 2),
        "minus_di": round(float(minus_di[-1]), 2),
    }
