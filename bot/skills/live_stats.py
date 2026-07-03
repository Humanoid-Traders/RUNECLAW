"""Single source of truth for live closed-trade filtering + win-rate.

Every status/portfolio card (the /start card, the Portfolio card, /performance,
the cockpit, …) used to re-implement this same filter + win-rate inline. Those
copies drifted and produced DIFFERENT win rates for the same account (reported:
38% on one card, 52% on another). This module is the one place that logic lives,
so the number is computed identically everywhere.

A "real" closed trade excludes:
  - adopted/injected bookkeeping artifacts (trade_id prefixes), and
  - never-filled orders (canceled / expired / price_drift / rejected) which
    close at $0 PnL and would otherwise pad the denominator and drag win rate
    down without ever counting as a win.
A win is net PnL strictly > 0.
"""

from __future__ import annotations

# Bookkeeping trade_ids that are not real trades.
ORPHAN_PREFIXES = ("TI-adopted", "TI-injected")
# Close reasons for orders that never became a fill (zero-PnL non-trades).
NON_TRADE_REASONS = frozenset(
    {"canceled", "cancelled", "expired", "price_drift", "rejected"})


def real_closed_trades(closed) -> list:
    """The closed positions that count as real trades (artifacts/non-fills out)."""
    out = []
    for t in (closed or []):
        tid = getattr(t, "trade_id", "") or ""
        if any(tid.startswith(p) for p in ORPHAN_PREFIXES):
            continue
        if getattr(t, "close_reason", "") in NON_TRADE_REASONS:
            continue
        out.append(t)
    return out


def live_win_stats(closed) -> dict:
    """Canonical live stats for a closed-position list.

    Returns {trades, total, wins, win_rate} where win_rate is a percentage
    (0–100) over the filtered real-trade set — identical everywhere it is used.
    """
    trades = real_closed_trades(closed)
    total = len(trades)
    wins = sum(1 for t in trades if (getattr(t, "pnl_usd", 0) or 0) > 0)
    win_rate = (wins / total * 100.0) if total else 0.0
    return {"trades": trades, "total": total, "wins": wins, "win_rate": win_rate}
