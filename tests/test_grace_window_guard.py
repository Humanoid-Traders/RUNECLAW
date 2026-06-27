"""
Grace-window guard (roadmap risk-depth #1): a just-opened position whose
exchange stop has not yet been placed must not stay blind until the next scan
tick (~10-60s on a leveraged perp). The monitor runs a tight, BOUNDED inline
sub-loop that either gets the exchange stop on, or closes the position locally
the instant price breaches the intended stop.

The sub-loop is inline on the single monitor task (no background task, no
double-close race) and capped at ``unprotected_guard_max_iterations`` so it can
never wedge monitoring of the other positions.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _executor():
    exe = LiveExecutor()
    exe._is_uta = True
    exe._save_positions = MagicMock(return_value=None)
    exe.close_position = AsyncMock(return_value="CLOSED")
    # Default: stop placement keeps failing so the local-breach path is exercised.
    exe._place_sl_tp = AsyncMock(return_value=(None, None))
    return exe


def _ex(last_price=100.0):
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": last_price})
    return ex


def _unprotected_long(price_stop=98.0, tp=110.0):
    # entry 100, SL 98, TP 110, and NO exchange stop id yet.
    return LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=price_stop, take_profit=tp, atr_at_entry=2.0, status="open",
    )


def _unprotected_short(price_stop=102.0, tp=90.0):
    return LivePosition(
        trade_id="T2", symbol="ETH/USDT:USDT", direction="SHORT",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=price_stop, take_profit=tp, atr_at_entry=2.0, status="open",
    )


# ── no-wait helper: the bounded loop's sleeps must not slow the suite ──
def _no_sleep():
    return patch("bot.core.live_executor.asyncio.sleep", new=AsyncMock())


class TestPlacesStop:
    def test_places_exchange_stop_and_does_not_close(self):
        exe = _executor()
        exe._place_sl_tp = AsyncMock(return_value=("SL-123", "TP-456"))
        ex = _ex(last_price=100.0)
        pos = _unprotected_long()
        msg = _run(exe._guard_unprotected_grace(ex, pos))
        # Stop got placed on the first pass -> no local close, ids recorded.
        assert msg is None
        assert pos.sl_order_id == "SL-123"
        assert pos.tp_order_id == "TP-456"
        exe.close_position.assert_not_awaited()
        exe._place_sl_tp.assert_awaited()  # at least once


class TestClosesOnBreach:
    def test_long_breach_closes_when_stop_cannot_place(self):
        exe = _executor()  # _place_sl_tp -> (None, None)
        ex = _ex(last_price=97.0)  # below the 98 stop
        pos = _unprotected_long()
        with _no_sleep():
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg == "CLOSED"
        exe.close_position.assert_awaited_once()
        args = exe.close_position.await_args.args
        assert args[0] == "T1"               # trade_id
        assert "SL HIT" in args[1]           # reason
        assert "grace sub-loop" in args[1]

    def test_short_breach_closes(self):
        exe = _executor()
        ex = _ex(last_price=103.0)  # above the 102 short stop
        pos = _unprotected_short()
        with _no_sleep():
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg == "CLOSED"
        exe.close_position.assert_awaited_once()

    def test_tp_breach_closes(self):
        exe = _executor()
        ex = _ex(last_price=111.0)  # above the 110 TP
        pos = _unprotected_long()
        with _no_sleep():
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg == "CLOSED"
        assert "TP HIT" in exe.close_position.await_args.args[1]


class TestNoFalseClose:
    def test_price_inside_band_does_not_close(self):
        exe = _executor()
        ex = _ex(last_price=100.5)  # between SL 98 and TP 110
        pos = _unprotected_long()
        with _no_sleep():
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg is None
        exe.close_position.assert_not_awaited()

    def test_zero_ticker_price_does_not_close(self):
        exe = _executor()
        ex = _ex(last_price=0.0)  # bad/missing price must never trigger a close
        pos = _unprotected_long()
        with _no_sleep():
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg is None
        exe.close_position.assert_not_awaited()


class TestBounded:
    def test_iterations_are_capped(self):
        exe = _executor()  # never places, never breaches
        ex = _ex(last_price=100.5)
        pos = _unprotected_long()
        with _no_sleep():
            _run(exe._guard_unprotected_grace(ex, pos))
        cap = CONFIG.execution.unprotected_guard_max_iterations
        # One ticker fetch per iteration, never more than the cap.
        assert ex.fetch_ticker.await_count <= cap
        assert ex.fetch_ticker.await_count == cap  # ran the full (bounded) loop
        # Placement retried each pass too, still bounded.
        assert exe._place_sl_tp.await_count <= cap

    def test_disabled_returns_immediately(self):
        exe = _executor()
        ex = _ex(last_price=97.0)  # would breach if the guard ran
        pos = _unprotected_long()
        # CONFIG.execution is a frozen dataclass; swap the whole CONFIG for the
        # method's lookup so the disabled early-return is exercised.
        with patch("bot.core.live_executor.CONFIG") as mock_cfg:
            mock_cfg.execution.unprotected_guard_enabled = False
            msg = _run(exe._guard_unprotected_grace(ex, pos))
        assert msg is None
        exe.close_position.assert_not_awaited()
        ex.fetch_ticker.assert_not_awaited()
        exe._place_sl_tp.assert_not_awaited()


class TestLocalStopBreached:
    """Pin the pure breach predicate the sub-loop and the per-tick check share."""
    def test_long_sl(self):
        exe = _executor()
        pos = _unprotected_long()
        assert exe._local_stop_breached(pos, 97.9) == (True, "SL HIT")
        assert exe._local_stop_breached(pos, 98.0) == (True, "SL HIT")
        assert exe._local_stop_breached(pos, 98.1)[0] is False

    def test_long_tp(self):
        exe = _executor()
        pos = _unprotected_long()
        assert exe._local_stop_breached(pos, 110.0) == (True, "TP HIT")
        assert exe._local_stop_breached(pos, 109.9)[0] is False

    def test_short_sl_and_tp(self):
        exe = _executor()
        pos = _unprotected_short()
        assert exe._local_stop_breached(pos, 102.1) == (True, "SL HIT")
        assert exe._local_stop_breached(pos, 90.0) == (True, "TP HIT")
        assert exe._local_stop_breached(pos, 100.0)[0] is False

    def test_zero_levels_never_breach(self):
        exe = _executor()
        pos = _unprotected_long(price_stop=0.0, tp=0.0)
        # Unset SL/TP (0.0) must never read as an instant hit at any price.
        assert exe._local_stop_breached(pos, 50.0)[0] is False
        assert exe._local_stop_breached(pos, 0.5)[0] is False

    def test_nonpositive_price_never_breach(self):
        exe = _executor()
        pos = _unprotected_long()
        assert exe._local_stop_breached(pos, 0.0)[0] is False
        assert exe._local_stop_breached(pos, -5.0)[0] is False


class TestWiring:
    def test_guard_called_in_check_positions(self):
        import inspect
        src = inspect.getsource(LiveExecutor.check_positions)
        assert "_guard_unprotected_grace" in src

    def test_guard_runs_before_per_tick_warning(self):
        import inspect
        src = inspect.getsource(LiveExecutor.check_positions)
        guard_at = src.index("_guard_unprotected_grace")
        warn_at = src.index("running local SL monitoring immediately")
        # The sub-loop must run BEFORE we fall through to the per-tick monitor.
        assert guard_at < warn_at
