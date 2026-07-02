"""Duplicate-close hardening (ops tip): reconcile_positions must serialize
with close_position()'s per-trade lock.

close_position() has held a per-trade asyncio.Lock since C2-02, and
reconcile_positions() had status-based guards ("closing"/"closed" skips +
closed_trades dedup) — but it read pos.status WITHOUT the lock, so a close
starting between the status snapshot and the reconciliation finalization could
be processed twice (double notification / conflicting PnL writes). The
reconciliation close block now (a) defers when the trade's close lock is held
and (b) runs its finalization under that lock.
"""
from __future__ import annotations

import inspect

from bot.core.live_executor import LiveExecutor


def test_reconcile_acquires_per_trade_close_lock():
    src = inspect.getsource(LiveExecutor.reconcile_positions)
    # Uses the SAME lock registry close_position uses...
    assert "_close_locks" in src
    # ...defers when a close is mid-flight instead of racing it...
    assert "locked()" in src
    # ...and holds it across the finalization.
    assert "async with _rec_lock" in src


def test_close_position_still_locks():
    src = inspect.getsource(LiveExecutor.close_position)
    assert "_close_locks" in src
    assert "async with lock" in src
