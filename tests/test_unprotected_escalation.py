"""
Persistently-unprotected position escalation (roadmap risk-depth #2).

An adopted/emergency position whose exchange stop still cannot be placed used
to be alerted exactly ONCE (at adoption) and the `unprotected` runtime marker
was never cleared once protection was finally established. This:
  * clears the stale `unprotected` marker the moment an exchange stop exists, and
  * re-alerts the operator on a throttle while a position stays stop-less,
    routing the alert through the monitor's returned message list.

Adopted positions are never force-closed (deliberate); the local static SL
check remains the close-on-breach backstop, so these tests assert ALERTING
behaviour, not closing.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from bot.config import CONFIG
from bot.core.live_executor import LiveExecutor, LivePosition

UTC = timezone.utc


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _executor(place_returns=(None, None)):
    """Executor whose exchange-touching calls are stubbed. By default SL/TP
    placement keeps FAILING (returns (None, None)) so the position stays
    unprotected and the escalation path runs."""
    exe = LiveExecutor()
    exe._is_uta = True
    exe._get_exchange = AsyncMock(return_value=_ex())
    exe._place_sl_tp = AsyncMock(return_value=place_returns)
    exe._run_partial_tp = AsyncMock(return_value=None)
    exe._save_positions = MagicMock(return_value=None)
    exe._record_warning = MagicMock(return_value=None)
    exe.close_position = AsyncMock(return_value="CLOSED")
    exe.adopt_exchange_positions = AsyncMock(return_value=[])
    # Skip the periodic exchange sync at the end of the loop.
    import time as _t
    exe._last_exchange_sync = _t.time()
    return exe


def _ex(last_price=100.5):
    ex = AsyncMock()
    ex.fetch_ticker = AsyncMock(return_value={"last": last_price})
    return ex


def _pos(*, sl_order_id=None, tp_order_id=None, unprotected=False, age_min=5):
    # entry 100, SL 98, TP 110; price 100.5 sits inside the band (no static close).
    p = LivePosition(
        trade_id="T1", symbol="BTC/USDT:USDT", direction="LONG",
        entry_price=100.0, quantity=1.0, cost_usd=100.0,
        stop_loss=98.0, take_profit=110.0, status="open",
    )
    p.opened_at = datetime.now(UTC) - timedelta(minutes=age_min)  # past 90s grace
    p.sl_order_id = sl_order_id
    p.tp_order_id = tp_order_id
    if unprotected:
        p.unprotected = True
    return p


def _check(exe, pos, price=100.5):
    exe._get_exchange = AsyncMock(return_value=_ex(price))
    exe._positions = {pos.trade_id: pos}
    return _run(exe.check_positions())


class TestEscalation:
    def test_unprotected_position_re_alerts_operator(self):
        exe = _executor()  # placement keeps failing
        pos = _pos(sl_order_id=None)
        msgs = _check(exe, pos)
        # An operator-facing UNPROTECTED alert is surfaced via the message list.
        assert any("UNPROTECTED POSITION" in m for m in msgs)
        assert pos.unprotected is True
        exe._record_warning.assert_called_with("unprotected_persist")
        # Not force-closed.
        exe.close_position.assert_not_awaited()

    def test_alert_is_throttled(self):
        exe = _executor()
        pos = _pos(sl_order_id=None)
        first = _check(exe, pos)
        second = _check(exe, pos)  # immediately again -> within the throttle window
        assert any("UNPROTECTED POSITION" in m for m in first)
        assert not any("UNPROTECTED POSITION" in m for m in second)

    def test_throttle_window_elapsed_re_alerts(self):
        exe = _executor()
        pos = _pos(sl_order_id=None)
        _check(exe, pos)
        # Backdate the last-alert stamp beyond the interval -> alert again.
        pos._unprotected_alert_at -= (CONFIG.execution.unprotected_alert_interval_s + 1)
        again = _check(exe, pos)
        assert any("UNPROTECTED POSITION" in m for m in again)

    def test_disabled_suppresses_escalation(self):
        exe = _executor()
        pos = _pos(sl_order_id=None)
        with patch("bot.core.live_executor.CONFIG") as mock_cfg:
            # Keep the rest of the loop's CONFIG reads truthy-but-disabled where it
            # matters: escalation OFF, partial-TP/trailing/time-stop OFF.
            mock_cfg.execution.unprotected_escalation_enabled = False
            mock_cfg.partial_tp.enabled = False
            mock_cfg.trailing.enabled = False
            mock_cfg.time_stop.enabled = False
            msgs = _check(exe, pos)
        assert not any("UNPROTECTED POSITION" in m for m in msgs)


class TestFlagClearing:
    def test_stale_unprotected_marker_cleared_when_stop_exists(self):
        exe = _executor()
        # Stop IS on the venue now, but the position is still flagged (stale).
        pos = _pos(sl_order_id="SL-1", tp_order_id="TP-1", unprotected=True)
        msgs = _check(exe, pos)
        assert pos.unprotected is False
        # And no escalation alert, since it is protected.
        assert not any("UNPROTECTED POSITION" in m for m in msgs)

    def test_protected_position_never_escalates(self):
        exe = _executor()
        pos = _pos(sl_order_id="SL-1", tp_order_id="TP-1")
        msgs = _check(exe, pos)
        assert not any("UNPROTECTED POSITION" in m for m in msgs)
        exe._record_warning.assert_not_called()


class TestWiring:
    def test_check_positions_has_escalation_and_clear(self):
        import inspect
        src = inspect.getsource(LiveExecutor.check_positions)
        assert "unprotected_escalation" in src
        assert "unprotected_cleared" in src
        # The flag-clear must run before the grace block (single early point).
        assert src.index("unprotected_cleared") < src.index("SAFEGUARD 2")
