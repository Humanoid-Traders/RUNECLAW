"""
Duplicate-close-booking guard (live incident 2026-07-07): one BTC SHORT
(entry 63105) was booked closed TWICE ~1 minute apart — once by the software
stop (exit 63,244.30, "Closed") and again by a second internal record slipping
the trade_id-only guards (exit 63,239.10, "STOP-LOSS HIT") — double-counting
realized PnL, trade count, loss streak, and the learning store. The adoption
sweeps can mint a second record for one exchange position under a different
trade_id (TI-adopted-…/ORPHAN-…/drifted clientOid), so guards must match by
close SIGNATURE (symbol + direction + entry ≤0.05% + booked ≤10 min ago), not
by trade_id alone.
"""
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.core.live_executor import LiveExecutor, LivePosition


def _executor():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex._closed_trades = []
    ex._positions = {}
    # _suppress_duplicate_record calls _save_positions; keep tests disk-free.
    ex._save_positions = lambda: None
    ex._save_closed_trades = lambda: None
    return ex


def _pos(trade_id="TI-orig", symbol="BTC/USDT:USDT", direction="SHORT",
         entry=63105.0, status="open", closed_at=None, pnl=None):
    return LivePosition(
        trade_id=trade_id, symbol=symbol, direction=direction,
        entry_price=entry, quantity=0.0015, cost_usd=9.47,
        stop_loss=63231.0, take_profit=59000.0, leverage=10,
        status=status, closed_at=closed_at, pnl_usd=pnl,
    )


# ── the exact live incident ──────────────────────────────────────────
def test_second_record_same_close_is_duplicate():
    """Booking #1 exists under TI-orig; the adopted duplicate (different id,
    same symbol/direction/entry, booked a minute ago) must be flagged."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", status="closed",
        closed_at=datetime.now(UTC) - timedelta(seconds=60), pnl=-0.28))
    dup = _pos(trade_id="TI-adopted-BTCUSDT-1751871480")
    assert ex._is_duplicate_close_booking(dup) is True


def test_same_trade_id_is_not_duplicate():
    """Same-id replacement is the existing, allowed behaviour."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", status="closed",
        closed_at=datetime.now(UTC) - timedelta(seconds=60), pnl=-0.28))
    same = _pos(trade_id="TI-orig")
    assert ex._is_duplicate_close_booking(same) is False


def test_symbol_normalization_matches_suffix_variants():
    """BTC/USDT:USDT vs BTCUSDT must be treated as the same instrument."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", symbol="BTC/USDT:USDT", status="closed",
        closed_at=datetime.now(UTC) - timedelta(seconds=60)))
    dup = _pos(trade_id="ORPHAN-14582688", symbol="BTCUSDT")
    assert ex._is_duplicate_close_booking(dup) is True


# ── legitimate cases must NOT be flagged ─────────────────────────────
def test_different_direction_not_duplicate():
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", direction="SHORT", status="closed",
        closed_at=datetime.now(UTC) - timedelta(seconds=60)))
    other = _pos(trade_id="TI-new", direction="LONG")
    assert ex._is_duplicate_close_booking(other) is False


def test_different_entry_price_not_duplicate():
    """A genuine re-entry at a meaningfully different price is a new trade."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", entry=63105.0, status="closed",
        closed_at=datetime.now(UTC) - timedelta(seconds=60)))
    reentry = _pos(trade_id="TI-new", entry=63400.0)  # 0.47% away
    assert ex._is_duplicate_close_booking(reentry) is False


def test_old_booking_outside_window_not_duplicate():
    """The same-price signature only binds near-in-time (2h window)."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", status="closed",
        closed_at=datetime.now(UTC) - timedelta(hours=3)))
    later = _pos(trade_id="TI-new")
    assert ex._is_duplicate_close_booking(later) is False


def test_sweep_lag_duplicate_30min_is_caught():
    """Live incident (UNI, 2026-07-11): the duplicate record's close was booked
    by a reconcile sweep 30 MINUTES after the first booking — outside the old
    10-min window, so the operator got an identical second close card. The
    widened 2h window must catch it."""
    ex = _executor()
    ex._closed_trades.append(_pos(
        trade_id="TI-orig", symbol="UNI/USDT", direction="LONG",
        entry=3.5270, status="closed",
        closed_at=datetime.now(UTC) - timedelta(minutes=30), pnl=-0.11))
    dup = _pos(trade_id="TI-adopted-UNI-1", symbol="UNI/USDT:USDT",
               direction="LONG", entry=3.5270)
    assert ex._is_duplicate_close_booking(dup) is True


# ── fill-time duplicate guard (UNI double "TRADE OPENED") ────────────
def test_duplicate_fill_detected_across_symbol_formats():
    """The bot's record (UNI/USDT) went open; the adopted duplicate
    (UNI/USDT:USDT) fills on the same exchange order → duplicate."""
    ex = _executor()
    ex._positions["TI-orig"] = _pos(
        trade_id="TI-orig", symbol="UNI/USDT", direction="LONG",
        entry=3.5270, status="open")
    dup = _pos(trade_id="ORPHAN-abc123", symbol="UNI/USDT:USDT",
               direction="LONG", entry=3.5270, status="pending_fill")
    assert ex._is_duplicate_fill(dup, 3.5270) is True


def test_fill_on_different_price_not_duplicate():
    ex = _executor()
    ex._positions["TI-orig"] = _pos(
        trade_id="TI-orig", symbol="UNI/USDT", direction="LONG",
        entry=3.5270, status="open")
    other = _pos(trade_id="TI-new", symbol="UNI/USDT:USDT",
                 direction="LONG", entry=3.60, status="pending_fill")
    assert ex._is_duplicate_fill(other, 3.60) is False


def test_fill_different_direction_not_duplicate():
    ex = _executor()
    ex._positions["TI-orig"] = _pos(
        trade_id="TI-orig", symbol="UNI/USDT", direction="SHORT",
        entry=3.5270, status="open")
    other = _pos(trade_id="TI-new", symbol="UNI/USDT:USDT",
                 direction="LONG", entry=3.5270, status="pending_fill")
    assert ex._is_duplicate_fill(other, 3.5270) is False


def test_fill_ignores_non_open_records():
    """Only records already OPEN count — a closed record at the same entry
    (e.g. yesterday's trade) must not suppress a legitimate new fill."""
    ex = _executor()
    ex._positions["TI-old"] = _pos(
        trade_id="TI-old", symbol="UNI/USDT", direction="LONG",
        entry=3.5270, status="closed")
    fresh = _pos(trade_id="TI-new", symbol="UNI/USDT:USDT",
                 direction="LONG", entry=3.5270, status="pending_fill")
    assert ex._is_duplicate_fill(fresh, 3.5270) is False


def test_fill_same_trade_id_not_duplicate():
    ex = _executor()
    p = _pos(trade_id="TI-orig", symbol="UNI/USDT", direction="LONG",
             entry=3.5270, status="open")
    ex._positions["TI-orig"] = p
    assert ex._is_duplicate_fill(p, 3.5270) is False


def test_empty_ledger_not_duplicate():
    ex = _executor()
    assert ex._is_duplicate_close_booking(_pos()) is False


# ── suppression + persistence backstop ───────────────────────────────
def test_suppress_marks_closed_without_booking():
    ex = _executor()
    dup = _pos(trade_id="TI-adopted-x")
    ex._suppress_duplicate_record(dup)
    assert dup.status == "closed"
    assert dup.close_reason == "duplicate_suppressed"
    assert ex._closed_trades == []  # nothing booked — no double-counted PnL


def test_append_backstop_skips_duplicate_row():
    """Even if a future path reaches _append_closed_trade with a duplicate,
    the persisted ledger must not gain a second PnL row."""
    ex = _executor()
    first = _pos(trade_id="TI-orig", status="closed",
                 closed_at=datetime.now(UTC), pnl=-0.28)
    ex._append_closed_trade(first)
    assert len(ex._closed_trades) == 1

    dup = _pos(trade_id="TI-adopted-BTCUSDT-1751871480", status="closed",
               closed_at=datetime.now(UTC), pnl=-0.28)
    ex._append_closed_trade(dup)
    assert len(ex._closed_trades) == 1  # duplicate row skipped

    # Same-id re-append still REPLACES (existing behaviour preserved).
    revised = _pos(trade_id="TI-orig", status="closed",
                   closed_at=datetime.now(UTC), pnl=-0.30)
    ex._append_closed_trade(revised)
    assert len(ex._closed_trades) == 1
    assert ex._closed_trades[0].pnl_usd == -0.30


def test_append_allows_distinct_trades():
    """Two genuinely different closes (different symbols) both persist."""
    ex = _executor()
    ex._append_closed_trade(_pos(trade_id="TI-a", status="closed",
                                 closed_at=datetime.now(UTC)))
    ex._append_closed_trade(_pos(trade_id="TI-b", symbol="ETH/USDT:USDT",
                                 entry=2500.0, status="closed",
                                 closed_at=datetime.now(UTC)))
    assert len(ex._closed_trades) == 2
