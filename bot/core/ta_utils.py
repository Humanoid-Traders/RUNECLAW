"""
RUNECLAW Technical Analysis Utilities -- shared functions used across modules.

Extracted to avoid circular imports between analyzer.py, multi_timeframe.py,
and other core modules that need EMA, ADX, and Regime.
"""

from __future__ import annotations

from enum import Enum

import numpy as np


class Regime(str, Enum):
    """Market regime classification based on ADX + directional movement.

    EXPANSION: volatility breakout from compression (squeeze release).
    Detected when ADX crosses above 20 while Bollinger Bandwidth is
    expanding after a contraction — signals the start of a new directional
    move.  Position sizing can increase in this regime.
    """
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    EXPANSION = "EXPANSION"
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


def rsi_series(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Canonical Wilder RSI over the full array (audit fix #20 consolidation).

    Warm-up indices (< period+1) are 50.0 (neutral). Zero-loss windows read
    100.0; a perfectly flat window (no gains, no losses) reads 50.0.
    """
    closes = np.asarray(closes, dtype=float)
    rsi = np.full(len(closes), 50.0)
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    if len(gains) < period:
        return rsi
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def obv_series(closes: np.ndarray, volumes: np.ndarray,
               seed_first: bool = True) -> np.ndarray:
    """Canonical On-Balance Volume (audit fix #20 consolidation).

    ``seed_first`` seeds obv[0] with volumes[0] (the analyzer's convention);
    False seeds 0 (the divergence scanner's legacy convention). The seed is a
    constant offset, so slopes/divergence comparisons are identical either way.
    Equal closes carry the previous value forward.
    """
    closes = np.asarray(closes, dtype=float)
    volumes = np.asarray(volumes, dtype=float)
    obv = np.zeros(len(closes))
    if len(closes) == 0:
        return obv
    obv[0] = volumes[0] if seed_first and len(volumes) else 0.0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv[i] = obv[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            obv[i] = obv[i - 1] - volumes[i]
        else:
            obv[i] = obv[i - 1]
    return obv


def macd_histogram_series(closes: np.ndarray, fast: int = 12, slow: int = 26,
                          signal: int = 9) -> np.ndarray:
    """Canonical MACD histogram over the full array (audit fix #20)."""
    closes = np.asarray(closes, dtype=float)
    if len(closes) < slow + signal:
        return np.zeros(len(closes))
    macd_line = _ema(closes, fast) - _ema(closes, slow)
    return macd_line - _ema(macd_line, signal)


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
