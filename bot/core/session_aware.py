"""
RUNECLAW Session-Aware Trading — adjusts risk and behavior based on
the current global trading session (Asian, London, New York, overlap).

Crypto trades 24/7 but volume and volatility vary significantly by session:
  - Asian (00:00-08:00 UTC): lowest volume, range-bound, wider spreads
  - London (08:00-16:00 UTC): increasing volume, breakouts common
  - NY (13:00-21:00 UTC): highest volume during London/NY overlap
  - London/NY Overlap (13:00-16:00 UTC): peak liquidity, tightest spreads
  - Late NY / Pre-Asian (21:00-00:00 UTC): declining volume, mean-reversion

Session adjustments:
  - Position size multiplier (reduce in low-liquidity sessions)
  - SL width multiplier (widen in low-liquidity to avoid noise stops)
  - Confidence bonus/penalty for session alignment
  - Friday PM risk reduction for weekend gap exposure
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from bot.compat import UTC
from typing import Optional


@dataclass
class SessionInfo:
    """Current trading session context."""
    session_name: str          # "asian", "london", "new_york", "london_ny_overlap", "late_ny"
    size_multiplier: float     # 1.0 = normal, 0.7 = reduced
    sl_width_multiplier: float # 1.0 = normal, 1.3 = wider
    confidence_adjustment: float  # added to blended confidence
    is_peak_liquidity: bool    # True during London/NY overlap
    is_weekend_risk: bool      # True on Friday after 18:00 UTC
    description: str           # human-readable


# Session definitions: (start_hour_utc, end_hour_utc, name, size_mult, sl_mult, conf_adj)
_SESSIONS = [
    (0,  8,  "asian",             0.75, 1.25, -0.03),  # low vol, wider SL, slight penalty
    (8,  13, "london",            1.00, 1.00,  0.00),  # normal
    (13, 16, "london_ny_overlap", 1.10, 0.90,  0.02),  # peak liquidity, tighter SL, bonus
    (16, 21, "new_york",          1.00, 1.00,  0.00),  # normal
    (21, 24, "late_ny",           0.80, 1.20, -0.02),  # declining vol
]


def get_current_session(now: Optional[datetime] = None) -> SessionInfo:
    """Determine the current global trading session and return adjustments."""
    if now is None:
        now = datetime.now(UTC)

    hour = now.hour
    weekday = now.weekday()  # 0=Mon, 4=Fri, 5=Sat, 6=Sun

    # Find matching session
    session_name = "unknown"
    size_mult = 1.0
    sl_mult = 1.0
    conf_adj = 0.0

    for start, end, name, sm, slm, ca in _SESSIONS:
        if start <= hour < end:
            session_name = name
            size_mult = sm
            sl_mult = slm
            conf_adj = ca
            break

    is_peak = session_name == "london_ny_overlap"

    # Weekend risk: Friday after 18:00 UTC, all Saturday, all Sunday
    is_weekend_risk = False
    if weekday == 4 and hour >= 18:  # Friday evening
        is_weekend_risk = True
        size_mult *= 0.6  # 40% reduction
        sl_mult *= 1.3    # wider stops for gap risk
        conf_adj -= 0.05  # penalty
    elif weekday in (5, 6):  # Saturday, Sunday
        is_weekend_risk = True
        size_mult *= 0.5  # 50% reduction
        sl_mult *= 1.4    # even wider stops
        conf_adj -= 0.07  # larger penalty

    # Build description
    descriptions = {
        "asian": "Asian session (low volume, range-bound)",
        "london": "London session (increasing volume)",
        "london_ny_overlap": "London/NY overlap (peak liquidity)",
        "new_york": "New York session",
        "late_ny": "Late NY / Pre-Asian (declining volume)",
    }
    desc = descriptions.get(session_name, "Unknown session")
    if is_weekend_risk:
        desc += " | WEEKEND RISK"

    return SessionInfo(
        session_name=session_name,
        size_multiplier=round(size_mult, 2),
        sl_width_multiplier=round(sl_mult, 2),
        confidence_adjustment=round(conf_adj, 3),
        is_peak_liquidity=is_peak,
        is_weekend_risk=is_weekend_risk,
        description=desc,
    )


def session_size_adjustment(base_size: float, session: Optional[SessionInfo] = None) -> float:
    """Apply session-based position size adjustment."""
    if session is None:
        session = get_current_session()
    return round(base_size * session.size_multiplier, 2)


def session_sl_adjustment(sl_distance: float, session: Optional[SessionInfo] = None) -> float:
    """Widen SL distance for low-liquidity sessions."""
    if session is None:
        session = get_current_session()
    return round(sl_distance * session.sl_width_multiplier, 8)
