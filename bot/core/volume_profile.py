"""
RUNECLAW Volume Profile Analysis — POC, VAH, VAL computation.

Volume Profile distributes traded volume across price levels to identify:
  - POC (Point of Control): price level with highest traded volume
  - VAH (Value Area High): upper boundary of 70% volume zone
  - VAL (Value Area Low): lower boundary of 70% volume zone

Trading applications:
  - Entries near POC have better fill quality (highest liquidity)
  - SL placed outside Value Area is more robust (less noise)
  - POC acts as dynamic support/resistance
  - Price outside Value Area signals potential trend/breakout
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class VolumeProfileResult:
    """Volume profile analysis result."""
    poc: float              # Point of Control (highest volume price)
    vah: float              # Value Area High
    val: float              # Value Area Low
    poc_volume: float       # Volume at POC level
    total_volume: float     # Total volume in profile
    value_area_pct: float   # Actual % of volume within VA (target 70%)
    price_vs_poc: str       # "above", "below", "at" (within 0.3%)
    price_in_value_area: bool  # True if current price within VA
    profile_skew: str       # "bullish" (POC in upper half), "bearish", "neutral"
    bins: int               # Number of price bins used
    description: str


def compute_volume_profile(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    num_bins: int = 50,
    value_area_pct: float = 70.0,
    current_price: Optional[float] = None,
) -> Optional[VolumeProfileResult]:
    """Compute volume profile from OHLCV data.

    Uses typical price (H+L+C)/3 to distribute volume across price bins.

    Args:
        highs, lows, closes: price arrays
        volumes: volume array
        num_bins: number of price levels to bin into
        value_area_pct: percentage of volume for value area (default 70%)
        current_price: current price for relative position analysis

    Returns:
        VolumeProfileResult or None if insufficient data.
    """
    if len(closes) < 10 or len(volumes) < 10:
        return None

    # Ensure arrays are same length
    n = min(len(highs), len(lows), len(closes), len(volumes))
    highs = np.asarray(highs[:n], dtype=float)
    lows = np.asarray(lows[:n], dtype=float)
    closes = np.asarray(closes[:n], dtype=float)
    volumes = np.asarray(volumes[:n], dtype=float)

    # Use typical price for volume distribution
    typical_prices = (highs + lows + closes) / 3.0

    price_min = float(np.min(lows))
    price_max = float(np.max(highs))

    if price_max <= price_min:
        return None

    # Create price bins
    bin_edges = np.linspace(price_min, price_max, num_bins + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0

    # Distribute volume across bins
    vol_profile = np.zeros(num_bins)
    for i in range(n):
        # Find which bin this typical price falls into
        bin_idx = int((typical_prices[i] - price_min) / (price_max - price_min) * num_bins)
        bin_idx = max(0, min(num_bins - 1, bin_idx))
        vol_profile[bin_idx] += volumes[i]

        # Also distribute to nearby bins based on high-low range
        # This gives a more realistic volume distribution
        lo_bin = int((lows[i] - price_min) / (price_max - price_min) * num_bins)
        hi_bin = int((highs[i] - price_min) / (price_max - price_min) * num_bins)
        lo_bin = max(0, min(num_bins - 1, lo_bin))
        hi_bin = max(0, min(num_bins - 1, hi_bin))

        if hi_bin > lo_bin:
            spread_vol = volumes[i] * 0.3  # 30% of volume spread across range
            per_bin = spread_vol / (hi_bin - lo_bin)
            for b in range(lo_bin, hi_bin + 1):
                if b != bin_idx:  # don't double-count the primary bin
                    vol_profile[b] += per_bin

    total_volume = float(np.sum(vol_profile))
    if total_volume <= 0:
        return None

    # POC: highest volume price level
    poc_idx = int(np.argmax(vol_profile))
    poc = float(bin_centers[poc_idx])
    poc_volume = float(vol_profile[poc_idx])

    # Value Area: expand from POC until 70% of volume is captured
    va_volume = poc_volume
    va_low_idx = poc_idx
    va_high_idx = poc_idx
    target_volume = total_volume * (value_area_pct / 100.0)

    while va_volume < target_volume:
        # Look at bins above and below, add the one with more volume
        can_go_up = va_high_idx < num_bins - 1
        can_go_down = va_low_idx > 0

        if not can_go_up and not can_go_down:
            break

        up_vol = vol_profile[va_high_idx + 1] if can_go_up else -1
        down_vol = vol_profile[va_low_idx - 1] if can_go_down else -1

        if up_vol >= down_vol:
            va_high_idx += 1
            va_volume += vol_profile[va_high_idx]
        else:
            va_low_idx -= 1
            va_volume += vol_profile[va_low_idx]

    vah = float(bin_edges[va_high_idx + 1])  # upper edge of high bin
    val = float(bin_edges[va_low_idx])        # lower edge of low bin
    actual_va_pct = (va_volume / total_volume * 100) if total_volume > 0 else 0

    # Current price analysis
    cp = current_price if current_price is not None else float(closes[-1])

    poc_diff_pct = abs(cp - poc) / poc * 100 if poc > 0 else 0
    if poc_diff_pct <= 0.3:
        price_vs_poc = "at"
    elif cp > poc:
        price_vs_poc = "above"
    else:
        price_vs_poc = "below"

    price_in_va = val <= cp <= vah

    # Profile skew
    mid_price = (price_min + price_max) / 2
    if poc > mid_price * 1.02:
        profile_skew = "bullish"
    elif poc < mid_price * 0.98:
        profile_skew = "bearish"
    else:
        profile_skew = "neutral"

    # Description
    desc_parts = [f"POC=${poc:,.4f}"]
    desc_parts.append(f"VA=[${val:,.4f}-${vah:,.4f}]")
    desc_parts.append(f"Price {price_vs_poc} POC")
    if price_in_va:
        desc_parts.append("within Value Area")
    else:
        desc_parts.append("OUTSIDE Value Area")

    return VolumeProfileResult(
        poc=round(poc, 8),
        vah=round(vah, 8),
        val=round(val, 8),
        poc_volume=round(poc_volume, 2),
        total_volume=round(total_volume, 2),
        value_area_pct=round(actual_va_pct, 1),
        price_vs_poc=price_vs_poc,
        price_in_value_area=price_in_va,
        profile_skew=profile_skew,
        bins=num_bins,
        description=" | ".join(desc_parts),
    )


def volume_profile_to_confluence(
    vp: VolumeProfileResult,
    direction: str,
) -> tuple[list[float], list[float]]:
    """Convert volume profile analysis into confluence votes.

    Rules:
      - LONG near VAL/POC (support) -> bullish vote
      - SHORT near VAH/POC (resistance) -> bearish vote
      - Price outside VA in trade direction -> momentum confirmation
      - Price outside VA against trade direction -> contrarian warning

    Returns:
        (votes, weights) for confluence scorer
    """
    votes: list[float] = []
    weights: list[float] = []

    is_long = direction == "LONG"

    # POC proximity
    if vp.price_vs_poc == "at":
        # At POC: supports mean-reversion, slight edge for direction of skew
        if vp.profile_skew == "bullish" and is_long:
            votes.append(1.0)
            weights.append(0.5)
        elif vp.profile_skew == "bearish" and not is_long:
            votes.append(-1.0)
            weights.append(0.5)

    # Value Area position
    if not vp.price_in_value_area:
        # Outside Value Area — breakout territory
        if vp.price_vs_poc == "above" and is_long:
            votes.append(1.0)
            weights.append(0.7)  # above VA + LONG = momentum confirmation
        elif vp.price_vs_poc == "below" and not is_long:
            votes.append(-1.0)
            weights.append(0.7)  # below VA + SHORT = momentum confirmation
        elif vp.price_vs_poc == "above" and not is_long:
            votes.append(-1.0)
            weights.append(0.4)  # above VA + SHORT = contrarian (weaker)
        elif vp.price_vs_poc == "below" and is_long:
            votes.append(1.0)
            weights.append(0.4)  # below VA + LONG = contrarian

    return votes, weights


def poc_magnet_signal(
    current_price: float,
    poc_price: float,
    atr: float,
) -> Optional[dict]:
    """Determine if POC is acting as a price magnet.

    If price is within 2x ATR of POC, it tends to be attracted toward it.
    Returns direction and strength of the magnet pull, or None if too far.
    """
    if poc_price <= 0 or atr <= 0:
        return None

    distance = current_price - poc_price
    dist_atr = abs(distance) / atr

    if dist_atr > 2.0:
        return None  # too far to exert pull

    # Strength decays linearly: strongest at 0 ATR distance, zero at 2 ATR
    strength = max(0.0, 1.0 - dist_atr / 2.0)

    direction = "pull_down" if distance > 0 else "pull_up"

    return {
        "direction": direction,
        "strength": round(strength, 3),
        "distance_atr": round(dist_atr, 3),
        "poc": poc_price,
    }
