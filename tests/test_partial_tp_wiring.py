"""
Partial take-profit ladder wired into the live monitor (operator opt-in to bank
profit early and fix the realized R:R asymmetry).

Verifies the ladder execution in isolation (the SL-order placement is stubbed):
  TP1 @ 1.5R  -> close 50%, SL -> breakeven
  TP2 @ 2.5R  -> close 30%, SL -> lock 1R
  SL only ratchets (never loosens); runner is left to the static SL check.
"""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock

from bot.core.live_executor import LiveExecutor, LivePosition


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ex():
    ex = AsyncMock()
    ex.create_order = AsyncMock(return_value={"id": "P1", "average": 0, "filled": 0})
    ex.amount_to_precision = MagicMock(side_effect=lambda symbol, amount: float(amount))
    return ex


def _executor():
    ex = _ex()
    exe = LiveExecutor()
    exe._is_uta = True
    exe._update_exchange_sl = AsyncMock(return_value=None)  # isolate ladder logic
    exe._save_positions = MagicMock(return_value=None)
    return exe, ex


def _long_pos():
    # entry 100, SL 98 -> initial risk 2.  1.5R = 103, 2.5R = 105, 1R-lock = 102.
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=98.0, take_profit=110.0, atr_at_entry=2.0, status="open",
    )


class TestPartialTPLadder:
    def test_tp1_closes_half_and_moves_sl_to_breakeven(self):
        exe, ex = _executor()
        pos = _long_pos()
        _run(exe._run_partial_tp(ex, pos, price=103.0))  # 1.5R
        # 50% of original qty closed via a reduceOnly market sell.
        ex.create_order.assert_awaited_once()
        kw = ex.create_order.await_args.kwargs
        assert kw["side"] == "sell"
        assert kw["params"]["reduceOnly"] is True
        assert kw["amount"] == pytest.approx(0.5)
        assert pos.quantity == pytest.approx(0.5)
        # SL ratcheted up to ~breakeven (entry + small fee buffer), from 98.
        assert pos.stop_loss > 99.9
        assert pos.partial_tp_state["tp1_hit"] is True
        exe._update_exchange_sl.assert_awaited()

    def test_tp2_closes_30pct_and_locks_1r(self):
        exe, ex = _executor()
        pos = _long_pos()
        _run(exe._run_partial_tp(ex, pos, price=103.0))   # TP1
        _run(exe._run_partial_tp(ex, pos, price=105.0))   # TP2 @ 2.5R
        # 30% of ORIGINAL qty closed at TP2.
        assert pos.quantity == pytest.approx(0.2, abs=1e-6)
        # SL locked at AT LEAST entry + 1R = 102 (the runner trail, which fires in
        # the same tick after TP2, may ratchet it higher — both lock profit).
        assert pos.stop_loss >= 102.0 - 1e-9
        assert pos.partial_tp_state["tp2_hit"] is True

    def test_sl_never_lowers(self):
        exe, ex = _executor()
        pos = _long_pos()
        _run(exe._run_partial_tp(ex, pos, price=103.0))   # SL -> ~100.1
        sl_after_tp1 = pos.stop_loss
        # Price dips back below 1.5R — no new TP, SL must NOT drop.
        _run(exe._run_partial_tp(ex, pos, price=101.0))
        assert pos.stop_loss == pytest.approx(sl_after_tp1)
        assert pos.stop_loss >= sl_after_tp1

    def test_no_action_before_tp1(self):
        exe, ex = _executor()
        pos = _long_pos()
        _run(exe._run_partial_tp(ex, pos, price=101.0))  # 0.5R, below TP1
        ex.create_order.assert_not_awaited()
        assert pos.quantity == pytest.approx(1.0)
        assert pos.stop_loss == pytest.approx(98.0)

    def test_wired_into_check_positions(self):
        import inspect
        src = inspect.getsource(LiveExecutor.check_positions)
        assert "_run_partial_tp" in src
        assert "CONFIG.partial_tp.enabled" in src
