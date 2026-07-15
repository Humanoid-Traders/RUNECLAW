"""
Tier 1b: SL/TP self-heal — re-place stops that went missing during operation.

verify_and_fix_sltp() is the idempotent re-placement routine; previously it only
ran at startup, so a position that became naked mid-session stayed naked until the
next restart. The engine now calls it periodically. These tests cover the
re-placement logic itself (no network — exchange + _place_sl_tp are stubbed).
"""

import pytest

from bot.core.live_executor import LiveExecutor, LivePosition


def _pos(trade_id="T1", sl_id=None, tp_id=None, sl=95.0, tp=110.0, status="open"):
    return LivePosition(
        trade_id=trade_id, symbol="BTC/USDT", direction="LONG", entry_price=100.0,
        quantity=1.0, cost_usd=100.0, stop_loss=sl, take_profit=tp,
        sl_order_id=sl_id, tp_order_id=tp_id, status=status,
    )


def _exec(tmp_path, monkeypatch, place_result=("sl1", "tp1")):
    e = LiveExecutor(state_dir=str(tmp_path))

    async def _fake_exchange():
        return object()

    calls = {"place": 0}

    async def _fake_place(exchange, symbol, direction, qty, sl, tp):
        calls["place"] += 1
        return place_result

    monkeypatch.setattr(e, "_get_exchange", _fake_exchange)
    monkeypatch.setattr(e, "_place_sl_tp", _fake_place)
    return e, calls


@pytest.mark.asyncio
async def test_replaces_missing_stop(tmp_path, monkeypatch):
    e, calls = _exec(tmp_path, monkeypatch)
    p = _pos(sl_id=None, tp_id=None)
    e._positions[p.trade_id] = p
    await e.verify_and_fix_sltp()
    assert calls["place"] == 1
    assert p.sl_order_id == "sl1" and p.tp_order_id == "tp1"


@pytest.mark.asyncio
async def test_protected_position_untouched(tmp_path, monkeypatch):
    e, calls = _exec(tmp_path, monkeypatch)
    p = _pos(sl_id="A", tp_id="B")           # distinct, valid ids -> protected
    e._positions[p.trade_id] = p
    await e.verify_and_fix_sltp()
    assert calls["place"] == 0                # not re-placed
    assert p.sl_order_id == "A" and p.tp_order_id == "B"


@pytest.mark.asyncio
async def test_combined_id_replaced_only_when_confirmed_missing(tmp_path, monkeypatch):
    # v3 combined order shares one id. The self-heal must NOT blindly
    # re-place (that cancels-then-places = a naked window every cycle). It
    # re-places ONLY when the exchange positively confirms the stop is gone.
    e, calls = _exec(tmp_path, monkeypatch, place_result=("X", "X"))

    async def _stop_missing(pos):
        return False  # exchange confirms NO stop attached

    monkeypatch.setattr(e, "_stop_live_on_exchange", _stop_missing)
    p = _pos(sl_id="X", tp_id="X")
    e._positions[p.trade_id] = p
    await e.verify_and_fix_sltp()
    assert calls["place"] == 1


@pytest.mark.asyncio
async def test_combined_id_left_alone_when_stop_live(tmp_path, monkeypatch):
    # A healthy combined stop (exchange confirms present, or can't verify)
    # must be left untouched — no cancel-then-replace naked window.
    for _verdict in (True, None):
        e, calls = _exec(tmp_path, monkeypatch, place_result=("X", "X"))

        async def _stop_state(pos, _v=_verdict):
            return _v

        monkeypatch.setattr(e, "_stop_live_on_exchange", _stop_state)
        p = _pos(sl_id="X", tp_id="X")
        e._positions[p.trade_id] = p
        await e.verify_and_fix_sltp()
        assert calls["place"] == 0, f"re-placed despite verdict={_verdict}"


@pytest.mark.asyncio
async def test_closed_positions_skipped(tmp_path, monkeypatch):
    e, calls = _exec(tmp_path, monkeypatch)
    e._positions["c"] = _pos(trade_id="c", sl_id=None, tp_id=None, status="closed")
    await e.verify_and_fix_sltp()
    assert calls["place"] == 0


@pytest.mark.asyncio
async def test_no_levels_skipped(tmp_path, monkeypatch):
    # A position with no SL/TP levels (0) has nothing to place.
    e, calls = _exec(tmp_path, monkeypatch)
    p = _pos(sl_id="A", tp_id="B", sl=0.0, tp=0.0)
    e._positions[p.trade_id] = p
    await e.verify_and_fix_sltp()
    assert calls["place"] == 0


@pytest.mark.asyncio
async def test_no_open_positions_is_noop(tmp_path, monkeypatch):
    e, calls = _exec(tmp_path, monkeypatch)
    await e.verify_and_fix_sltp()             # empty book
    assert calls["place"] == 0
