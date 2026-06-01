"""
Volume Profile analysis for RUNECLAW.

Computes Point of Control (POC), Value Area High/Low from OHLCV data.
POC is the price level where the most volume traded — acts as a magnet
for price reversion. Value Area contains 70% of traded volume.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import Optional


@dataclass
class VolumeProfileResult:
    """Volume profile analysis output."""
    poc_price: float           # Point of Control — highest volume price level
    value_area_high: float     # Upper bound of 70% volume zone
    value_area_low: float      # Lower bound of 70% volume zone
    total_volume: float        # Total volume in profile
    num_levels: int            # Number of price bins
    price_vs_poc: str          # "above" | "below" | "at_poc"
    poc_distance_pct: float    # Distance from current price to POC as %


def compute_volume_profile(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    volumes: np.ndarray,
    num_bins: int = 50,
    value_area_pct: float = 0.70,
) -> Optional[VolumeProfileResult]:
    """Compute volume profile from OHLCV data.

    Args:
        closes: Close prices array
        highs: High prices array
        lows: Low prices array
        volumes: Volume array
        num_bins: Number of price bins for profile
        value_area_pct: Percentage of volume for value area (default 70%)

    Returns:
        VolumeProfileResult or None if insufficient data
    """
    if len(closes) < 10 or len(volumes) < 10:
        return None

    total_vol = float(np.sum(volumes))
    if total_vol <= 0:
        return None

    # Price range for binning
    price_min = float(np.min(lows))
    price_max = float(np.max(highs))
    if price_max <= price_min:
        return None

    # Create price bins
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

    # Distribute volume across bins using typical price
    typical_prices = (highs + lows + closes) / 3
    vol_profile = np.zeros(num_bins)

    for i in range(len(typical_prices)):
        bin_idx = int((typical_prices[i] - price_min) / (price_max - price_min) * (num_bins - 1))
        bin_idx = max(0, min(num_bins - 1, bin_idx))
        vol_profile[bin_idx] += volumes[i]

    # POC: bin with maximum volume
    poc_idx = int(np.argmax(vol_profile))
    poc_price = float(bin_centers[poc_idx])

    # Value Area: expand from POC until 70% of volume is captured
    va_volume = vol_profile[poc_idx]
    target_volume = total_vol * value_area_pct
    low_idx = poc_idx
    high_idx = poc_idx

    while va_volume < target_volume:
        # Expand to whichever adjacent bar has more volume
        expand_low = vol_profile[low_idx - 1] if low_idx > 0 else 0
        expand_high = vol_profile[high_idx + 1] if high_idx < num_bins - 1 else 0

        if expand_low == 0 and expand_high == 0:
            break

        if expand_low >= expand_high and low_idx > 0:
            low_idx -= 1
            va_volume += vol_profile[low_idx]
        elif high_idx < num_bins - 1:
            high_idx += 1
            va_volume += vol_profile[high_idx]
        else:
            low_idx -= 1
            va_volume += vol_profile[low_idx]

    va_high = float(bin_edges[high_idx + 1])
    va_low = float(bin_edges[low_idx])

    # Current price position relative to POC
    current = float(closes[-1])
    poc_dist_pct = ((current - poc_price) / poc_price * 100) if poc_price > 0 else 0

    if abs(poc_dist_pct) < 0.5:
        position = "at_poc"
    elif current > poc_price:
        position = "above"
    else:
        position = "below"

    return VolumeProfileResult(
        poc_price=round(poc_price, 6),
        value_area_high=round(va_high, 6),
        value_area_low=round(va_low, 6),
        total_volume=round(total_vol, 2),
        num_levels=num_bins,
        price_vs_poc=position,
        poc_distance_pct=round(poc_dist_pct, 4),
    )


def poc_magnet_signal(
    current_price: float,
    poc_price: float,
    atr: float,
    magnet_threshold_atr: float = 1.5,
) -> dict:
    """Compute POC magnet reversion signal.

    When price is within magnet_threshold_atr × ATR of POC, there's a
    statistical tendency for price to revert toward POC (highest volume node).

    Args:
        current_price: current market price
        poc_price: Point of Control from volume profile
        atr: current ATR value
        magnet_threshold_atr: distance in ATR multiples to consider "in range"

    Returns:
        dict with magnet analysis: active, direction, distance, strength
    """
    if atr <= 0 or poc_price <= 0 or current_price <= 0:
        return {"active": False, "direction": "none", "distance_atr": 0, "strength": 0}

    distance = current_price - poc_price
    distance_atr = abs(distance) / atr

    in_range = distance_atr <= magnet_threshold_atr

    if not in_range:
        return {
            "active": False,
            "direction": "none",
            "distance_atr": round(distance_atr, 2),
            "strength": 0,
        }

    # Direction: price above POC = expect pull down, below = expect pull up
    if distance > 0:
        direction = "pull_down"  # price above POC, expect reversion down
    elif distance < 0:
        direction = "pull_up"   # price below POC, expect reversion up
    else:
        direction = "at_poc"

    # Strength: inverse of distance (closer = stronger magnet)
    strength = round(max(0, 1.0 - (distance_atr / magnet_threshold_atr)), 4)

    return {
        "active": True,
        "direction": direction,
        "distance_atr": round(distance_atr, 2),
        "strength": strength,
        "poc_price": round(poc_price, 6),
    }
