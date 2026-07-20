"""The grace machinery must key off ACTUAL fill time, not placement time.

A limit order can rest for hours, so `opened_at` (placement time) was always
past the 90s grace gate by fill time — the grace branch and its bounded
re-protection sub-loop NEVER engaged for limit fills. The fill transitions now
stamp `filled_at`, and the gate in check_positions prefers it.
"""
import inspect
from datetime import UTC, datetime, timedelta

from bot.core.live_executor import LiveExecutor, LivePosition


def test_grace_gate_prefers_filled_at_over_opened_at():
    src = inspect.getsource(LiveExecutor.check_positions)
    i_ref = src.index('getattr(pos, "filled_at", None) or pos.opened_at')
    i_gate = src.index("if age_secs < 90:")
    assert i_ref < i_gate, "grace age must derive from filled_at when present"


def test_all_deferred_open_transitions_stamp_filled_at():
    # Every path that flips a position to "open" OUTSIDE the synchronous market
    # entry must stamp the actual fill time: limit fill, drift fallback, and
    # both cancel-race branches.
    for meth, expect in [
        (LiveExecutor._check_pending_limit, 1),
        (LiveExecutor._execute_drift_market_fallback, 1),
        (LiveExecutor._close_position_inner, 2),   # the two cancel races
    ]:
        src = inspect.getsource(meth)
        n = src.count('setattr(pos, "filled_at", datetime.now(UTC))')
        assert n >= expect, f"{meth.__name__}: expected >={expect} filled_at stamps, found {n}"


def test_cancel_races_attempt_protection():
    # The cancel-race open transitions previously placed NO exchange stop at
    # all. They must now attempt placement and mark unprotected on failure.
    src = inspect.getsource(LiveExecutor._close_position_inner)
    assert src.count("_place_sl_tp") >= 2, "both cancel races must attempt an exchange stop"
    assert src.count('setattr(pos, "unprotected", True)') >= 2


def test_grace_age_math_filled_at_fresh_opened_at_stale():
    # Mirror the gate's expression: a position opened (placed) 2h ago but filled
    # 5s ago must count as ~5s old for grace purposes.
    pos = LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=95.0, take_profit=110.0,
        status="open",
    )
    pos.opened_at = datetime.now(UTC) - timedelta(hours=2)
    setattr(pos, "filled_at", datetime.now(UTC) - timedelta(seconds=5))
    ref = getattr(pos, "filled_at", None) or pos.opened_at
    age = (datetime.now(UTC) - ref).total_seconds()
    assert age < 90, "freshly-filled position must be inside the grace window"
    # And without filled_at the same position is (correctly) outside it.
    delattr(pos, "filled_at") if hasattr(pos, "filled_at") else None
    ref2 = getattr(pos, "filled_at", None) or pos.opened_at
    assert (datetime.now(UTC) - ref2).total_seconds() > 90
