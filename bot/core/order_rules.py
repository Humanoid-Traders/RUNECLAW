"""
RUNECLAW Order Rules — asset-specific execution guards.

Implements GetClaw-style order intelligence for UTA accounts:
  - ISOLATED margin (mandatory for all assets)
  - Market hours awareness for metals/stocks
  - Weekend SL widening for gap-risk assets
  - Weekend size reduction for metals
  - TP/SL deferral for gap-risk limit orders

Uses the existing _classify_symbol() from market_scanner.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from bot.compat import UTC

logger = logging.getLogger(__name__)

# ── Market Hours (UTC) ─────────────────────────────────────────────
# Bitget trading windows — all times in UTC.
# Crypto: 24/7
# Metals (XAUUSDT etc.): Mon 00:01 – Fri 23:59 (closed Sat/Sun)
# Stock perps (TSLA, NVDA etc.): Mon–Fri ~02:30 – 09:00 UTC
#   (21:30 – 04:00 CST = 02:30 – 09:00 UTC next day, roughly)
# Commodities (CL etc.): Similar to metals

_ALWAYS_OPEN = {"Crypto"}
# Note: Pre-IPO tokens may have exchange-specific trading windows.
# Treated as 24/7 for now; monitor for maintenance-window rejections.
_PRE_IPO = {"Pre-IPO"}
_WEEKDAY_ONLY = {"Metal", "Commodity", "ETF"}  # closed weekends
_SESSION_HOURS = {"Stock"}  # specific daily window


def is_market_open(asset_class: str, now: datetime | None = None) -> tuple[bool, str]:
    """Check if the market is open for this asset class.

    Returns (is_open, reason_if_closed).
    """
    if now is None:
        now = datetime.now(UTC)

    weekday = now.weekday()  # 0=Mon, 6=Sun

    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return True, ""

    if asset_class in _WEEKDAY_ONLY:
        if weekday >= 5:  # Saturday or Sunday
            return False, f"{asset_class} markets are closed on weekends (opens Monday)"
        return True, ""

    if asset_class in _SESSION_HOURS:
        if weekday >= 5:
            return False, f"Stock perps are closed on weekends"
        # Stock perps: 02:30 – 09:00 UTC (US market hours during EDT)
        minutes_today = now.hour * 60 + now.minute
        # Stocks: 02:30 - 09:00 UTC (9:30 AM - 4:00 PM ET during EDT)
        if 150 <= minutes_today < 540:  # 150 = 2*60+30, 540 = 9*60
            return True, ""
        else:
            return False, f"Stock perps trade ~02:30–09:00 UTC (current: {now.hour:02d}:{now.minute:02d} UTC) — order will queue"

    # Unknown class — assume open
    return True, ""


def is_weekend_queued(asset_class: str, now: datetime | None = None) -> bool:
    """Return True if a limit order placed now would sit queued over weekend/off-hours."""
    if now is None:
        now = datetime.now(UTC)

    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return False

    is_open, _ = is_market_open(asset_class, now)
    return not is_open


def adjust_sl_for_gap_risk(
    stop_loss: float,
    entry_price: float,
    direction: str,
    asset_class: str,
    is_weekend: bool,
) -> float:
    """Widen SL for gap-risk assets when placed during off-hours.

    GetClaw rule: widen SL by ~25-50% for weekend-queued metals/stocks.
    - Normal SL: 2% → Weekend SL: 2.5-3%
    """
    if not is_weekend:
        return stop_loss
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return stop_loss

    sl_dist_pct = abs(entry_price - stop_loss) / entry_price if entry_price > 0 else 0

    # Widen by 40% (midpoint of GetClaw's 25-50% range)
    widen_factor = 1.40

    if direction.upper() == "LONG":
        # SL is below entry — move it further down
        new_sl_dist = sl_dist_pct * widen_factor
        new_sl = entry_price * (1 - new_sl_dist)
    else:
        # SHORT: SL is above entry — move it further up
        new_sl_dist = sl_dist_pct * widen_factor
        new_sl = entry_price * (1 + new_sl_dist)

    logger.info(
        "Gap-risk SL widened for %s: %.4f → %.4f (%.1f%% → %.1f%%)",
        asset_class, stop_loss, new_sl, sl_dist_pct * 100, new_sl_dist * 100,
    )
    return round(new_sl, 8)


def adjust_size_for_weekend(
    size_usd: float,
    asset_class: str,
    is_weekend: bool,
) -> float:
    """Reduce position size for weekend-queued metals (30-40% reduction).

    GetClaw rule: size down 30-40% for weekend gold limits.
    """
    if not is_weekend:
        return size_usd
    if asset_class not in _WEEKDAY_ONLY:
        return size_usd

    # 35% reduction (midpoint)
    reduction = 0.35
    new_size = size_usd * (1 - reduction)
    logger.info(
        "Weekend %s size reduced: $%.2f → $%.2f (%.0f%% reduction)",
        asset_class, size_usd, new_size, reduction * 100,
    )
    return round(new_size, 2)


def should_defer_tp_sl(asset_class: str, is_weekend: bool, order_type: str) -> bool:
    """Return True if TP/SL should be placed AFTER fill, not at placement.

    GetClaw rule: for weekend-queued limits on metals/stocks, set TP/SL
    after fill confirmation to avoid instant TP trigger on gap fill.
    """
    if order_type != "limit":
        return False
    if not is_weekend:
        return False
    if asset_class in _ALWAYS_OPEN or asset_class in _PRE_IPO:
        return False
    return True


# ── Quick reference: asset limits ──────────────────────────────────
ASSET_RULES = {
    "Metal":     {"min_sl_pct": 1.5, "weekend_sl_pct": 2.5, "max_leverage": 20},
    "Stock":     {"min_sl_pct": 2.0, "weekend_sl_pct": 3.0, "max_leverage": 10},
    "Commodity": {"min_sl_pct": 2.0, "weekend_sl_pct": 3.0, "max_leverage": 10},
    "ETF":       {"min_sl_pct": 2.0, "weekend_sl_pct": 2.5, "max_leverage": 10},
    "Crypto":    {"min_sl_pct": 1.0, "weekend_sl_pct": 1.0, "max_leverage": 125},
}
