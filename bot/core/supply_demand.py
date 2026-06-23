"""
RUNECLAW Supply/Demand Zone Mapper — institutional order block detection.

Identifies zones where price moved explosively away from, indicating
institutional supply/demand imbalance. When price returns to these zones,
high-probability reversal entries.

Zone types:
  - Demand Zone (bullish): price rallied sharply from this level (unfilled buy orders)
  - Supply Zone (bearish): price dropped sharply from this level (unfilled sell orders)
  - Fresh Zone: never retested (strongest)
  - Tested Zone: retested once and held (still valid)
  - Broken Zone: price broke through (invalidated)

Detection method:
  - Find explosive moves (>2x ATR in 1-2 candles)
  - The base candle before the move = the zone
  - Zone extends from candle low to candle high (for demand) or high to low (for supply)
  - Track retests and invalidation
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class SDZone:
    """A supply or demand zone."""
    zone_type: str       # "demand" or "supply"
    zone_high: float     # upper boundary
    zone_low: float      # lower boundary
    origin_index: int    # candle index where zone was created
    strength: float      # 0-1 (based on departure speed and volume)
    status: str          # "fresh", "tested", "broken"
    retests: int         # number of times price returned to zone
    departure_pct: float # how fast price left the zone (% move)
    volume_ratio: float  # volume at zone vs average

    @property
    def midpoint(self) -> float:
        return (self.zone_high + self.zone_low) / 2

    @property
    def width_pct(self) -> float:
        return (self.zone_high - self.zone_low) / self.zone_low * 100 if self.zone_low > 0 else 0


def detect_zones(
    opens: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    volumes: np.ndarray,
    atr: float,
    lookback: int = 100,
    min_departure_atr: float = 2.0,
) -> list[SDZone]:
    """Detect supply and demand zones from OHLCV data.

    Args:
        opens, highs, lows, closes, volumes: price arrays
        atr: current ATR for departure threshold
        lookback: bars to analyze
        min_departure_atr: minimum move in ATR multiples to qualify as explosive

    Returns:
        List of SDZone objects, sorted by strength.
    """
    zones: list[SDZone] = []

    n = min(len(opens), len(highs), len(lows), len(closes), len(volumes))
    if n < 10 or atr <= 0:
        return zones

    start = max(0, n - lookback)
    o = opens[start:n].astype(float)
    h = highs[start:n].astype(float)
    l = lows[start:n].astype(float)
    c = closes[start:n].astype(float)
    v = volumes[start:n].astype(float)

    avg_vol = float(np.mean(v)) if len(v) > 0 else 1.0
    min_move = atr * min_departure_atr

    for i in range(1, len(c) - 2):
        # Check for explosive UP move (demand zone = base before rally)
        move_up = c[i+1] - c[i]  # 1-candle move
        if i + 2 < len(c):
            move_up_2 = c[i+2] - c[i]  # 2-candle move
        else:
            move_up_2 = move_up

        best_up = max(move_up, move_up_2)

        if best_up >= min_move:
            # Base candle (i) is the demand zone
            zone = SDZone(
                zone_type="demand",
                zone_high=float(max(o[i], c[i])),  # body high
                zone_low=float(l[i]),                # candle low (full zone)
                origin_index=start + i,
                strength=0.0,
                status="fresh",
                retests=0,
                departure_pct=round(best_up / c[i] * 100, 2) if c[i] > 0 else 0,
                volume_ratio=round(float(v[i]) / avg_vol, 2) if avg_vol > 0 else 1.0,
            )
            zones.append(zone)

        # Check for explosive DOWN move (supply zone = base before drop)
        move_down = c[i] - c[i+1]
        if i + 2 < len(c):
            move_down_2 = c[i] - c[i+2]
        else:
            move_down_2 = move_down

        best_down = max(move_down, move_down_2)

        if best_down >= min_move:
            zone = SDZone(
                zone_type="supply",
                zone_high=float(h[i]),               # candle high (full zone)
                zone_low=float(min(o[i], c[i])),     # body low
                origin_index=start + i,
                strength=0.0,
                status="fresh",
                retests=0,
                departure_pct=round(best_down / c[i] * 100, 2) if c[i] > 0 else 0,
                volume_ratio=round(float(v[i]) / avg_vol, 2) if avg_vol > 0 else 1.0,
            )
            zones.append(zone)

    # Track retests and invalidation
    current_price = float(c[-1]) if len(c) > 0 else 0
    for zone in zones:
        _update_zone_status(zone, h, l, c, zone.origin_index - start, current_price)
        _compute_zone_strength(zone)

    # Remove broken zones and sort by strength
    zones = [z for z in zones if z.status != "broken"]
    zones.sort(key=lambda z: z.strength, reverse=True)

    return zones


def _update_zone_status(zone: SDZone, highs: np.ndarray, lows: np.ndarray,
                         closes: np.ndarray, origin_local: int, current_price: float) -> None:
    """Update zone status based on subsequent price action."""
    retests = 0

    for i in range(origin_local + 2, len(closes)):
        bar_low = float(lows[i])
        bar_high = float(highs[i])
        bar_close = float(closes[i])

        if zone.zone_type == "demand":
            # Price entered the zone
            if bar_low <= zone.zone_high:
                if bar_close > zone.zone_low:
                    retests += 1  # tested and held
                else:
                    zone.status = "broken"
                    zone.retests = retests
                    return
        else:  # supply
            if bar_high >= zone.zone_low:
                if bar_close < zone.zone_high:
                    retests += 1
                else:
                    zone.status = "broken"
                    zone.retests = retests
                    return

    zone.retests = retests
    if retests > 0:
        zone.status = "tested"
    else:
        zone.status = "fresh"


def _compute_zone_strength(zone: SDZone) -> None:
    """Compute zone strength score (0-1)."""
    strength = 0.30  # base

    # Departure speed (faster = stronger)
    if zone.departure_pct > 3.0:
        strength += 0.20
    elif zone.departure_pct > 1.5:
        strength += 0.10

    # Volume (higher = more institutional)
    if zone.volume_ratio > 2.0:
        strength += 0.15
    elif zone.volume_ratio > 1.5:
        strength += 0.08

    # Freshness (untested = strongest)
    if zone.status == "fresh":
        strength += 0.20
    elif zone.status == "tested" and zone.retests == 1:
        strength += 0.10
    elif zone.status == "tested" and zone.retests >= 2:
        strength += 0.0  # weakening with each retest

    # Narrow zones are more precise
    if zone.width_pct < 1.0:
        strength += 0.05

    zone.strength = min(0.95, round(strength, 3))


def find_nearest_zone(
    zones: list[SDZone],
    current_price: float,
    direction: str,
    max_distance_pct: float = 3.0,
) -> Optional[SDZone]:
    """Find the nearest relevant zone for a trade direction.

    For LONG: find nearest demand zone below current price.
    For SHORT: find nearest supply zone above current price.
    """
    candidates = []

    for zone in zones:
        if direction == "LONG" and zone.zone_type == "demand":
            if zone.zone_high < current_price:
                dist_pct = (current_price - zone.zone_high) / current_price * 100
                if dist_pct <= max_distance_pct:
                    candidates.append((dist_pct, zone))
        elif direction == "SHORT" and zone.zone_type == "supply":
            if zone.zone_low > current_price:
                dist_pct = (zone.zone_low - current_price) / current_price * 100
                if dist_pct <= max_distance_pct:
                    candidates.append((dist_pct, zone))

    if not candidates:
        return None

    # Return closest zone with sufficient strength
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def zones_to_confluence(
    zones: list[SDZone],
    current_price: float,
    direction: str,
) -> tuple[list[float], list[float]]:
    """Convert nearby zones to confluence votes."""
    votes: list[float] = []
    weights: list[float] = []

    zone = find_nearest_zone(zones, current_price, direction)
    if zone is None:
        return votes, weights

    # Strong zone nearby in trade direction = confirmation
    if direction == "LONG" and zone.zone_type == "demand":
        votes.append(1.0)
        weights.append(round(zone.strength * 0.9, 3))
    elif direction == "SHORT" and zone.zone_type == "supply":
        votes.append(-1.0)
        weights.append(round(zone.strength * 0.9, 3))

    return votes, weights
