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


def _trade_sign(t) -> str:
    """'win' (pnl>0), 'loss' (pnl<0) or 'flat' (breakeven / unknown)."""
    p = getattr(t, "pnl_usd", 0) or 0
    return "win" if p > 0 else ("loss" if p < 0 else "flat")


def _streak_from_trades(trades) -> dict:
    """The run of consecutive same-outcome trades ending at the most recent one.
    Assumes `trades` is chronological (oldest first) and walks back from the
    newest. A breakeven trade (pnl==0) ends any streak. Pure."""
    if not trades:
        return {"kind": None, "count": 0}
    newest = _trade_sign(trades[-1])
    if newest == "flat":
        return {"kind": None, "count": 0}
    count = 0
    for t in reversed(trades):
        if _trade_sign(t) == newest:
            count += 1
        else:
            break
    return {"kind": newest, "count": count}


def current_streak(closed) -> dict:
    """TG-2: {kind: 'win'|'loss'|None, count} over the filtered real-trade set."""
    return _streak_from_trades(real_closed_trades(closed))


def streak_badge(streak: dict) -> str:
    """A compact streak chip for status cards: '🔥 3W', '🧊 2L', or '' when
    there's no streak (a single trade isn't a streak — needs >= 2)."""
    if not streak or not streak.get("kind") or (streak.get("count") or 0) < 2:
        return ""
    n = streak["count"]
    return f"🔥 {n}W" if streak["kind"] == "win" else f"🧊 {n}L"


def live_win_stats(closed) -> dict:
    """Canonical live stats for a closed-position list.

    Returns {trades, total, wins, win_rate, streak} where win_rate is a
    percentage (0–100) over the filtered real-trade set — identical everywhere
    it is used. `streak` is {kind, count} for the current win/loss run.
    """
    trades = real_closed_trades(closed)
    total = len(trades)
    wins = sum(1 for t in trades if (getattr(t, "pnl_usd", 0) or 0) > 0)
    win_rate = (wins / total * 100.0) if total else 0.0
    return {"trades": trades, "total": total, "wins": wins,
            "win_rate": win_rate, "streak": _streak_from_trades(trades)}
