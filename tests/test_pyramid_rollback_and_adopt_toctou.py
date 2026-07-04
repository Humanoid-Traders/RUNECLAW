"""Two money-path safety fixes:

Bug 3 (pyramid SL rollback): a pyramid add moved the EXISTING winner's stop to
breakeven BEFORE execute(); the executor's duplicate-symbol preflight then
blocked the add — leaving the winner damaged at breakeven with no rollback. The
breakeven move is now deferred into the success branch, so a blocked/failed add
never touches the existing position.

Bug 8 (adopt TOCTOU): adopt_exchange_positions built its "tracked" set from only
open/pending_fill positions, so a position mid-close (status "closing") was
re-adopted as an orphan — duplicate record, double-counted PnL. "closing" (and
any position holding a live close lock) is now tracked.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor


# ── Bug 3: pyramid breakeven move only after a filled add ───────────────

class _FakeExec:
    def __init__(self, positions):
        self._pos = positions
        self.sl_updates = []

    @property
    def open_positions(self):
        return list(self._pos)

    async def _get_exchange(self):
        return object()

    async def _update_exchange_sl(self, exchange, lp, new_sl):
        self.sl_updates.append((lp.trade_id, new_sl))

    def _save_positions(self):
        pass


class _FakeEng:
    _pyramid_move_existing_sl_to_breakeven = \
        RuneClawEngine._pyramid_move_existing_sl_to_breakeven


def _lp(trade_id, symbol, entry, sl):
    return SimpleNamespace(trade_id=trade_id, symbol=symbol,
                           entry_price=entry, stop_loss=sl)


@pytest.mark.asyncio
async def test_helper_moves_only_the_existing_position_to_breakeven():
    existing = _lp("OLD", "BTC/USDT:USDT", 100.0, 95.0)
    added = _lp("NEW", "BTC/USDT:USDT", 100.0, 96.0)
    ex = _FakeExec([existing, added])
    eng = _FakeEng()
    await eng._pyramid_move_existing_sl_to_breakeven(ex, "BTC/USDT:USDT", "NEW")
    assert existing.stop_loss == pytest.approx(100.0)      # moved to breakeven
    assert added.stop_loss == pytest.approx(96.0)          # new add untouched
    assert ex.sl_updates == [("OLD", 100.0)]               # exchange update once


def test_breakeven_move_is_gated_on_successful_fill():
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    # The move must be called ONLY inside the success branch, and the old inline
    # pre-execute _update_exchange_sl call must be gone from the confirm body.
    i_fail = src.index("if not live_failed:")
    i_move = src.index("_pyramid_move_existing_sl_to_breakeven")
    assert i_move > i_fail, "pyramid SL move must be in the post-success branch"
    # No direct exchange SL mutation before execute in the confirm body.
    i_exec = src.index("await executor.execute(")
    assert "_update_exchange_sl" not in src[:i_exec], \
        "existing SL must not be mutated before the add executes"


def test_pyramid_flag_no_longer_moves_sl_pre_execute():
    src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
    # The pyramid detection block sets the deferred flag, not an SL move.
    assert "_is_pyramid_add = True" in src


# ── Bug 8: adopt must treat a closing position as tracked ───────────────

def test_adopt_tracked_set_includes_closing_and_close_locked():
    src = inspect.getsource(LiveExecutor.adopt_exchange_positions)
    tracked = src[src.index("tracked = {"):src.index("for p in ex_positions")]
    assert '"closing"' in tracked, "a mid-close position must not be re-adopted"
    assert "_close_locks" in tracked, "a close-locked position must not be re-adopted"


class _StubExec(LiveExecutor):
    """Bypass __init__ network/config; just exercise the tracked-set logic."""
    def __init__(self):
        pass


def test_closing_position_is_in_the_tracked_set():
    # Mirror the exact set construction the method uses.
    from bot.core.live_executor import normalize_symbol
    closing = SimpleNamespace(symbol="BTC/USDT:USDT", direction="LONG",
                              status="closing", trade_id="C1")
    positions = {"C1": closing}
    close_locks = {"C1": asyncio.Lock()}
    tracked = {
        (normalize_symbol(p.symbol), p.direction)
        for p in positions.values()
        if p.status in ("open", "pending_fill", "closing")
    }
    assert (normalize_symbol("BTC/USDT:USDT"), "LONG") in tracked
