"""
RUNECLAW Divergence Scanner — detects price/indicator divergences.

Scans for:
  - Regular Bullish Divergence: price makes lower low, indicator makes higher low
  - Regular Bearish Divergence: price makes higher high, indicator makes lower high
  - Hidden Bullish Divergence: price makes higher low, indicator makes lower low (trend continuation)
  - Hidden Bearish Divergence: price makes lower high, indicator makes higher high (trend continuation)

Supported indicators: RSI, MACD histogram, OBV.
Each divergence is scored by strength (number of bars between pivots, depth of divergence).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class DivergenceSignal:
    """A detected divergence between price and an indicator."""
    div_type: str        # "regular_bullish", "regular_bearish", "hidden_bullish", "hidden_bearish"
    indicator: str       # "rsi", "macd", "obv"
    confidence: float    # 0.0 - 1.0
    price_pivot1: float
    price_pivot2: float
    ind_pivot1: float
    ind_pivot2: float
    bars_apart: int
    description: str


def _find_local_extrema(data: np.ndarray, order: int = 5) -> tuple[list[int], list[int]]:
    """Find local minima and maxima indices.

    Args:
        data: 1D array of values
        order: number of points on each side to compare

    Returns:
        (minima_indices, maxima_indices)
    """
    minima = []
    maxima = []
    n = len(data)

    for i in range(order, n - order):
        # Check if local minimum
        is_min = True
        is_max = True
        for j in range(1, order + 1):
            if data[i] >= data[i - j] or data[i] >= data[i + j]:
                is_min = False
            if data[i] <= data[i - j] or data[i] <= data[i + j]:
                is_max = False
        if is_min:
            minima.append(i)
        if is_max:
            maxima.append(i)

    return minima, maxima


def _compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI from close prices."""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    rsi = np.full(len(closes), 50.0)  # default neutral

    if len(gains) < period:
        return rsi

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def _compute_macd_hist(closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9) -> np.ndarray:
    """Compute MACD histogram from close prices."""
    if len(closes) < slow + signal:
        return np.zeros(len(closes))

    def ema(data, period):
        result = np.zeros(len(data))
        result[0] = data[0]
        mult = 2.0 / (period + 1)
        for i in range(1, len(data)):
            result[i] = (data[i] - result[i-1]) * mult + result[i-1]
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line

    return histogram


def _compute_obv(closes: np.ndarray, volumes: np.ndarray) -> np.ndarray:
    """Compute On-Balance Volume."""
    obv = np.zeros(len(closes))
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv[i] = obv[i-1] + volumes[i]
        elif closes[i] < closes[i-1]:
            obv[i] = obv[i-1] - volumes[i]
        else:
            obv[i] = obv[i-1]
    return obv


def _check_divergence(
    price_data: np.ndarray,
    indicator_data: np.ndarray,
    lookback: int = 50,
    min_bars_apart: int = 5,
    indicator_name: str = "rsi",
) -> list[DivergenceSignal]:
    """Check for divergences between price and indicator within lookback window."""
    signals: list[DivergenceSignal] = []

    if len(price_data) < lookback or len(indicator_data) < lookback:
        return signals

    # Use only the lookback window
    price = price_data[-lookback:]
    ind = indicator_data[-lookback:]

    # AN-2: normalize divergence strength by the indicator's recent RANGE rather
    # than abs(i1_val). For indicators that cross or sit near zero (OBV cumulative,
    # MACD histogram) the old abs(i1_val)+1e-10 denominator collapsed to ~1e-10,
    # so any tiny divergence saturated strength to the cap. The window range is a
    # stable scale that can't approach zero unless the indicator is flat.
    _div_scale = max(float(np.max(ind) - np.min(ind)), 1e-9)

    order = max(3, min(7, lookback // 10))
    price_mins, price_maxs = _find_local_extrema(price, order)
    ind_mins, ind_maxs = _find_local_extrema(ind, order)

    # Regular Bullish: price lower low + indicator higher low
    if len(price_mins) >= 2:
        for i in range(len(price_mins) - 1):
            p1_idx, p2_idx = price_mins[i], price_mins[i + 1]
            bars = p2_idx - p1_idx
            if bars < min_bars_apart:
                continue

            if price[p2_idx] < price[p1_idx]:  # price made lower low
                # Find nearest indicator lows
                i1_val = ind[p1_idx]
                i2_val = ind[p2_idx]

                if i2_val > i1_val:  # indicator made higher low
                    strength = min(1.0, (i2_val - i1_val) / _div_scale * 5)
                    conf = min(0.90, 0.50 + strength * 0.30 + min(bars / 30, 0.10))
                    signals.append(DivergenceSignal(
                        div_type="regular_bullish",
                        indicator=indicator_name,
                        confidence=round(conf, 3),
                        price_pivot1=float(price[p1_idx]),
                        price_pivot2=float(price[p2_idx]),
                        ind_pivot1=float(i1_val),
                        ind_pivot2=float(i2_val),
                        bars_apart=bars,
                        description=f"Regular bullish divergence ({indicator_name.upper()}): "
                                    f"price LL, {indicator_name.upper()} HL over {bars} bars",
                    ))

    # Regular Bearish: price higher high + indicator lower high
    if len(price_maxs) >= 2:
        for i in range(len(price_maxs) - 1):
            p1_idx, p2_idx = price_maxs[i], price_maxs[i + 1]
            bars = p2_idx - p1_idx
            if bars < min_bars_apart:
                continue

            if price[p2_idx] > price[p1_idx]:  # price made higher high
                i1_val = ind[p1_idx]
                i2_val = ind[p2_idx]

                if i2_val < i1_val:  # indicator made lower high
                    strength = min(1.0, (i1_val - i2_val) / _div_scale * 5)
                    conf = min(0.90, 0.50 + strength * 0.30 + min(bars / 30, 0.10))
                    signals.append(DivergenceSignal(
                        div_type="regular_bearish",
                        indicator=indicator_name,
                        confidence=round(conf, 3),
                        price_pivot1=float(price[p1_idx]),
                        price_pivot2=float(price[p2_idx]),
                        ind_pivot1=float(i1_val),
                        ind_pivot2=float(i2_val),
                        bars_apart=bars,
                        description=f"Regular bearish divergence ({indicator_name.upper()}): "
                                    f"price HH, {indicator_name.upper()} LH over {bars} bars",
                    ))

    # Hidden Bullish: price higher low + indicator lower low (trend continuation)
    if len(price_mins) >= 2:
        for i in range(len(price_mins) - 1):
            p1_idx, p2_idx = price_mins[i], price_mins[i + 1]
            bars = p2_idx - p1_idx
            if bars < min_bars_apart:
                continue

            if price[p2_idx] > price[p1_idx]:  # price made higher low
                i1_val = ind[p1_idx]
                i2_val = ind[p2_idx]

                if i2_val < i1_val:  # indicator made lower low
                    strength = min(1.0, (i1_val - i2_val) / _div_scale * 5)
                    conf = min(0.85, 0.45 + strength * 0.25 + min(bars / 30, 0.10))
                    signals.append(DivergenceSignal(
                        div_type="hidden_bullish",
                        indicator=indicator_name,
                        confidence=round(conf, 3),
                        price_pivot1=float(price[p1_idx]),
                        price_pivot2=float(price[p2_idx]),
                        ind_pivot1=float(i1_val),
                        ind_pivot2=float(i2_val),
                        bars_apart=bars,
                        description=f"Hidden bullish divergence ({indicator_name.upper()}): "
                                    f"price HL, {indicator_name.upper()} LL — trend continuation",
                    ))

    # Hidden Bearish: price lower high + indicator higher high (trend continuation)
    if len(price_maxs) >= 2:
        for i in range(len(price_maxs) - 1):
            p1_idx, p2_idx = price_maxs[i], price_maxs[i + 1]
            bars = p2_idx - p1_idx
            if bars < min_bars_apart:
                continue

            if price[p2_idx] < price[p1_idx]:  # price made lower high
                i1_val = ind[p1_idx]
                i2_val = ind[p2_idx]

                if i2_val > i1_val:  # indicator made higher high
                    strength = min(1.0, (i2_val - i1_val) / _div_scale * 5)
                    conf = min(0.85, 0.45 + strength * 0.25 + min(bars / 30, 0.10))
                    signals.append(DivergenceSignal(
                        div_type="hidden_bearish",
                        indicator=indicator_name,
                        confidence=round(conf, 3),
                        price_pivot1=float(price[p1_idx]),
                        price_pivot2=float(price[p2_idx]),
                        ind_pivot1=float(i1_val),
                        ind_pivot2=float(i2_val),
                        bars_apart=bars,
                        description=f"Hidden bearish divergence ({indicator_name.upper()}): "
                                    f"price LH, {indicator_name.upper()} HH — trend continuation",
                    ))

    return signals


def scan_divergences(
    closes: np.ndarray,
    volumes: Optional[np.ndarray] = None,
    lookback: int = 50,
) -> list[DivergenceSignal]:
    """Scan for all divergences across RSI, MACD histogram, and OBV.

    Args:
        closes: array of close prices
        volumes: array of volumes (optional, needed for OBV)
        lookback: how many bars to look back for pivots

    Returns:
        List of DivergenceSignal objects, sorted by confidence descending.
    """
    all_signals: list[DivergenceSignal] = []

    if len(closes) < 30:
        return all_signals

    # RSI divergences
    rsi = _compute_rsi(closes)
    all_signals.extend(_check_divergence(closes, rsi, lookback, indicator_name="rsi"))

    # MACD histogram divergences
    macd_hist = _compute_macd_hist(closes)
    all_signals.extend(_check_divergence(closes, macd_hist, lookback, indicator_name="macd"))

    # OBV divergences (if volume available)
    if volumes is not None and len(volumes) == len(closes):
        obv = _compute_obv(closes, volumes)
        all_signals.extend(_check_divergence(closes, obv, lookback, indicator_name="obv"))

    # Sort by confidence descending
    all_signals.sort(key=lambda s: s.confidence, reverse=True)

    return all_signals


def divergence_to_confluence_votes(signals: list[DivergenceSignal]) -> tuple[list[float], list[float]]:
    """Convert divergence signals into confluence votes and weights.

    Regular bullish/hidden bullish → positive vote (bullish)
    Regular bearish/hidden bearish → negative vote (bearish)
    Weight scales with confidence and type (regular > hidden).

    Returns:
        (votes, weights) lists for confluence scorer
    """
    votes: list[float] = []
    weights: list[float] = []

    # Deduplicate: take best signal per type
    best_by_type: dict[str, DivergenceSignal] = {}
    for sig in signals:
        key = sig.div_type
        if key not in best_by_type or sig.confidence > best_by_type[key].confidence:
            best_by_type[key] = sig

    for sig in best_by_type.values():
        is_regular = sig.div_type.startswith("regular_")
        base_weight = 0.85 if is_regular else 0.65  # regular divergence stronger signal
        weight = base_weight * sig.confidence

        if "bullish" in sig.div_type:
            votes.append(1.0)
        else:
            votes.append(-1.0)
        weights.append(round(weight, 3))

    return votes, weights
