"""
Seasonality Analysis for RUNECLAW.

Detects time-of-day and day-of-week patterns in price action.
Uses historical candle timestamps to identify statistically favorable
trading windows (e.g., Asian session dips, US open volatility).

Data source: Candle timestamps already fetched via ccxt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# UTC hour ranges for major trading sessions
SESSIONS = {
    "ASIA": (0, 8),      # 00:00-08:00 UTC (Tokyo/Singapore)
    "EUROPE": (7, 16),   # 07:00-16:00 UTC (London)
    "US": (13, 22),      # 13:00-22:00 UTC (New York)
}

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


@dataclass
class SeasonalityResult:
    """Seasonality analysis output."""
    current_session: str          # "ASIA" | "EUROPE" | "US" | "OVERLAP" | "OFF"
    current_hour_utc: int
    current_day: str

    # Hour-of-day stats (from historical data)
    best_hour_utc: int            # hour with highest avg return
    worst_hour_utc: int           # hour with lowest avg return
    current_hour_bias: str        # "bullish" | "bearish" | "neutral"
    current_hour_avg_return: float  # avg return for current hour

    # Day-of-week stats
    best_day: str                 # day with highest avg return
    worst_day: str                # day with lowest avg return
    current_day_bias: str         # "bullish" | "bearish" | "neutral"
    current_day_avg_return: float

    # Overall signal
    seasonality_score: float      # -1.0 to 1.0 composite
    recommendation: str           # "FAVORABLE" | "UNFAVORABLE" | "NEUTRAL"


def detect_session(hour_utc: int) -> str:
    """Classify current UTC hour into trading session."""
    sessions_active = []
    for name, (start, end) in SESSIONS.items():
        if start <= hour_utc < end:
            sessions_active.append(name)

    if len(sessions_active) >= 2:
        return "OVERLAP"
    elif len(sessions_active) == 1:
        return sessions_active[0]
    else:
        return "OFF"


def analyze_seasonality(
    candles: list[list],
    current_time: Optional[datetime] = None,
) -> Optional[SeasonalityResult]:
    """Compute seasonality patterns from historical candle data.

    Args:
        candles: list of [timestamp_ms, open, high, low, close, volume]
        current_time: override for current time (for testing)

    Returns:
        SeasonalityResult or None if insufficient data
    """
    if len(candles) < 48:  # Need at least 2 days of hourly data
        return None

    now = current_time or datetime.now(timezone.utc)
    current_hour = now.hour
    current_day_name = DAYS[now.weekday()]
    current_session = detect_session(current_hour)

    # Parse candle data and compute returns
    hourly_returns: dict[int, list[float]] = {h: [] for h in range(24)}
    daily_returns: dict[int, list[float]] = {d: [] for d in range(7)}

    for i in range(1, len(candles)):
        try:
            ts = candles[i][0]
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            else:
                continue

            open_price = candles[i][1]
            close_price = candles[i][4]
            if open_price > 0:
                ret = (close_price - open_price) / open_price * 100
                hourly_returns[dt.hour].append(ret)
                daily_returns[dt.weekday()].append(ret)
        except (IndexError, TypeError, ValueError):
            continue

    # Compute averages per hour
    hour_avgs = {}
    for h in range(24):
        vals = hourly_returns[h]
        hour_avgs[h] = np.mean(vals) if vals else 0.0

    best_hour = max(hour_avgs, key=hour_avgs.get)
    worst_hour = min(hour_avgs, key=hour_avgs.get)
    current_hour_avg = hour_avgs.get(current_hour, 0.0)

    if current_hour_avg > 0.02:
        hour_bias = "bullish"
    elif current_hour_avg < -0.02:
        hour_bias = "bearish"
    else:
        hour_bias = "neutral"

    # Compute averages per day
    day_avgs = {}
    for d in range(7):
        vals = daily_returns[d]
        day_avgs[d] = np.mean(vals) if vals else 0.0

    best_day_idx = max(day_avgs, key=day_avgs.get)
    worst_day_idx = min(day_avgs, key=day_avgs.get)
    current_day_avg = day_avgs.get(now.weekday(), 0.0)

    if current_day_avg > 0.02:
        day_bias = "bullish"
    elif current_day_avg < -0.02:
        day_bias = "bearish"
    else:
        day_bias = "neutral"

    # Composite score: blend hour + day bias (-1 to 1)
    hour_score = np.clip(current_hour_avg / 0.1, -1, 1) if abs(current_hour_avg) > 0.001 else 0
    day_score = np.clip(current_day_avg / 0.1, -1, 1) if abs(current_day_avg) > 0.001 else 0
    composite = float(hour_score * 0.6 + day_score * 0.4)

    if composite > 0.2:
        recommendation = "FAVORABLE"
    elif composite < -0.2:
        recommendation = "UNFAVORABLE"
    else:
        recommendation = "NEUTRAL"

    return SeasonalityResult(
        current_session=current_session,
        current_hour_utc=current_hour,
        current_day=current_day_name,
        best_hour_utc=best_hour,
        worst_hour_utc=worst_hour,
        current_hour_bias=hour_bias,
        current_hour_avg_return=round(float(current_hour_avg), 4),
        best_day=DAYS[best_day_idx],
        worst_day=DAYS[worst_day_idx],
        current_day_bias=day_bias,
        current_day_avg_return=round(float(current_day_avg), 4),
        seasonality_score=round(composite, 4),
        recommendation=recommendation,
    )
