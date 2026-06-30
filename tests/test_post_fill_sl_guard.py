"""
RC-AUD-001 parity on the post-fill entry paths (limit-fill + drift→market fallback).

The synchronous market entry path retries once and FLATTENS when the stop-loss
can't be placed. The limit-fill and drift-fallback paths previously did neither —
the limit path warned only when BOTH legs failed and the drift path was silent —
so an SL-only failure left a live leveraged position with no exchange stop and no
operator alert until a later check_positions tick. `_reattempt_post_fill_sl` now
mirrors the market path's retry + unprotected-marker + alert (it does NOT flatten:
the grace sub-loop / escalation in check_positions is the designed remediation in
that monitoring context).

These cover the helper directly (no network — _place_sl_tp is stubbed).
"""

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition
from bot.utils.models import Direction


def _pos(sl_id=None, tp_id=None, sl=95.0, tp=110.0):
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=sl, take_profit=tp,
        sl_order_id=sl_id, tp_order_id=tp_id, status="open",
    )


def _exec(tmp_path, monkeypatch, place_results):
    """place_results: list of (sl, tp) returned on successive _place_sl_tp calls."""
    e = LiveExecutor(state_dir=str(tmp_path))
    seq = list(place_results)
    calls = {"place": 0}

    async def _fake_place(exchange, symbol, direction, qty, sl, tp):
        calls["place"] += 1
        return seq.pop(0) if seq else (None, None)

    monkeypatch.setattr(e, "_place_sl_tp", _fake_place)
    return e, calls


@pytest.mark.asyncio
async def test_sl_placed_first_try_is_noop(tmp_path, monkeypatch):
    # Common case: SL placed → no retry, ids unchanged, NOT marked unprotected.
    e, calls = _exec(tmp_path, monkeypatch, [("sl1", "tp1")])
    p = _pos()
    sl_id, tp_id = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, "sl1", "tp1", "T1")
    assert (sl_id, tp_id) == ("sl1", "tp1")
    assert calls["place"] == 0                 # no retry attempted
    assert getattr(p, "unprotected", False) is False


@pytest.mark.asyncio
async def test_sl_fails_then_retry_succeeds(tmp_path, monkeypatch):
    # SL None on the entry attempt; the single retry gets it on → protected.
    e, calls = _exec(tmp_path, monkeypatch, [("sl_retry", "tp_retry")])
    p = _pos()
    sl_id, tp_id = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id == "sl_retry"
    assert calls["place"] == 1                 # retried exactly once
    assert getattr(p, "unprotected", False) is False


@pytest.mark.asyncio
async def test_sl_only_failure_is_marked_unprotected(tmp_path, monkeypatch):
    # The gap: TP placed but SL is None. Must retry, and on persistent failure
    # mark unprotected (previously this was silent because TP was truthy).
    e, calls = _exec(tmp_path, monkeypatch, [(None, "tp2")])   # retry: still no SL
    p = _pos()
    sl_id, tp_id = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, "tp1", "T1")
    assert sl_id is None
    assert tp_id == "tp1"                       # original TP preserved
    assert calls["place"] == 1                  # retried once
    assert getattr(p, "unprotected", False) is True


@pytest.mark.asyncio
async def test_sl_fails_both_times_marked_unprotected(tmp_path, monkeypatch):
    e, calls = _exec(tmp_path, monkeypatch, [(None, None)])    # retry also fails
    p = _pos()
    sl_id, tp_id = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert calls["place"] == 1
    assert getattr(p, "unprotected", False) is True


@pytest.mark.asyncio
async def test_retry_exception_is_swallowed_and_marked(tmp_path, monkeypatch):
    e = LiveExecutor(state_dir=str(tmp_path))

    async def _boom(*a, **k):
        raise RuntimeError("venue error")

    monkeypatch.setattr(e, "_place_sl_tp", _boom)
    p = _pos()
    sl_id, _ = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None                        # fail-open, no raise
    assert getattr(p, "unprotected", False) is True


@pytest.mark.asyncio
async def test_no_stop_level_not_flagged(tmp_path, monkeypatch):
    # stop_loss == 0 means no stop was intended → no retry, no unprotected flag.
    e, calls = _exec(tmp_path, monkeypatch, [("x", "y")])
    p = _pos(sl=0.0, tp=0.0)
    sl_id, _ = await e._reattempt_post_fill_sl(
        object(), p, Direction.LONG, 1.0, None, None, "T1")
    assert sl_id is None
    assert calls["place"] == 0
    assert getattr(p, "unprotected", False) is False
