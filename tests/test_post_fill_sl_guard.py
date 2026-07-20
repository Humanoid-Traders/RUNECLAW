"""
RC-AUD-001 parity on the post-fill entry paths (limit-fill + drift→market fallback).

`_reattempt_post_fill_sl` is now a full ESCALATION LADDER with the same
end-state guarantee as the synchronous market path: on SL failure it retries
once, then runs the bounded grace sub-loop (re-protect / close on breach), then
FLATTENS — the position ends protected, closed, or with an URGENT manual-close
message. It never ends silently "unprotected (monitoring active)". Returns
``(sl_id, tp_id, close_msg)``; ``close_msg`` non-None means the ladder closed
the position (or the close failed) and callers must surface it.

These cover the helper directly (no network — _place_sl_tp, the grace
sub-loop, and close_position are stubbed).
"""

import pytest
from unittest.mock import AsyncMock

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.models import Direction


def _pos(sl_id=None, tp_id=None, sl=95.0, tp=110.0):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=sl, take_profit=tp,
        sl_order_id=sl_id, tp_order_id=tp_id, status="open",
    )


def _exec(tmp_path, monkeypatch, place_results, grace=None, close=None):
    """place_results: (sl, tp) tuples for successive _place_sl_tp calls.
    grace: async callable for _guard_unprotected_grace (default: no-op → None).
    close: AsyncMock for close_position (default returns "CLOSED test")."""
    e = LiveExecutor(state_dir=str(tmp_path))
    seq = list(place_results)
    calls = {"place": 0}

    async def _fake_place(exchange, symbol, direction, qty, sl, tp):
        calls["place"] += 1
        return seq.pop(0) if seq else (None, None)

    monkeypatch.setattr(e, "_place_sl_tp", _fake_place)

    async def _default_grace(exchange, pos):
        return None

    e._guard_unprotected_grace = grace or _default_grace
    e.close_position = close or AsyncMock(return_value="CLOSED test")
    return e, calls


@pytest.mark.asyncio
async def test_sl_placed_first_try_is_noop(tmp_path, monkeypatch):
    # Common case: SL placed → no retry, ids unchanged, no close, not flagged.
    e, calls = _exec(tmp_path, monkeypatch, [("sl1", "tp1")])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, "sl1", "tp1", "T1")
    assert (sl_id, tp_id, close_msg) == ("sl1", "tp1", None)
    assert calls["place"] == 0                 # no retry attempted
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_sl_fails_then_retry_succeeds(tmp_path, monkeypatch):
    # SL None on the entry attempt; the single retry gets it on → protected.
    e, calls = _exec(tmp_path, monkeypatch, [("sl_retry", "tp_retry")])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id == "sl_retry" and close_msg is None
    assert calls["place"] == 1                 # retried exactly once
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_fails_then_grace_places_stop(tmp_path, monkeypatch):
    # Retry fails → grace sub-loop gets the exchange stop on: protected, marker
    # cleared, grace's ids adopted, NO flatten.
    async def _grace_places(exchange, pos):
        pos.sl_order_id = "SL-G"
        return None

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_places)
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp1", "T1")
    assert sl_id == "SL-G" and close_msg is None
    assert getattr(p, "unprotected", False) is False   # cleared once protected
    e.close_position.assert_not_awaited()


@pytest.mark.asyncio
async def test_grace_breach_close_propagates_without_double_close(tmp_path, monkeypatch):
    # Grace closed the position on breach → its message propagates and the
    # flatten stage must NOT fire a second close.
    async def _grace_closed(exchange, pos):
        return "CLOSED_LOCAL breach msg"

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_closed)
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert close_msg == "CLOSED_LOCAL breach msg"
    e.close_position.assert_not_awaited()              # no double close


@pytest.mark.asyncio
async def test_grace_exhausted_flattens(tmp_path, monkeypatch):
    # Retry + grace both fail to protect → FLATTEN (RC-AUD-001 parity).
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)])
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once_with("T1", reason="sl_placement_failed")
    assert close_msg and "CLOSED for safety" in close_msg


@pytest.mark.asyncio
async def test_flatten_failure_returns_urgent_message(tmp_path, monkeypatch):
    # The safety close itself fails → URGENT manual-intervention message, no raise.
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)],
                     close=AsyncMock(side_effect=RuntimeError("venue down")))
    p = _pos()
    sl_id, tp_id, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert close_msg and "URGENT" in close_msg and "MANUALLY" in close_msg


@pytest.mark.asyncio
async def test_retry_exception_swallowed_ladder_continues(tmp_path, monkeypatch):
    # A raising retry must not abort the ladder — grace then flatten still run.
    e = LiveExecutor(state_dir=str(tmp_path))

    async def _boom(*a, **k):
        raise RuntimeError("venue error")

    monkeypatch.setattr(e, "_place_sl_tp", _boom)

    async def _no_grace(exchange, pos):
        return None

    e._guard_unprotected_grace = _no_grace
    e.close_position = AsyncMock(return_value="CLOSED test")
    p = _pos()
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once()
    assert close_msg is not None


@pytest.mark.asyncio
async def test_grace_exception_swallowed_flatten_still_runs(tmp_path, monkeypatch):
    # Even a raising grace sub-loop must not leave the position naked — flatten.
    async def _grace_boom(exchange, pos):
        raise RuntimeError("ticker outage")

    e, calls = _exec(tmp_path, monkeypatch, [(None, None)], grace=_grace_boom)
    p = _pos()
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    e.close_position.assert_awaited_once()
    assert close_msg is not None


@pytest.mark.asyncio
async def test_no_stop_level_never_flags_or_flattens(tmp_path, monkeypatch):
    # stop_loss == 0 means no stop intended → no retry, no grace, no flatten.
    e, calls = _exec(tmp_path, monkeypatch, [("x", "y")])
    p = _pos(sl=0.0, tp=0.0)
    sl_id, _, close_msg = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None and close_msg is None
    assert calls["place"] == 0
    assert getattr(p, "unprotected", False) is False
    e.close_position.assert_not_awaited()
