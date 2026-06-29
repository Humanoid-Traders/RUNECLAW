"""
Silent-safety-failure alerting: unprotected live positions.

When an open live position has NO exchange stop-loss after the grace window
(SL placement / self-heal FAILED), the monitor now fires a CRITICAL alert —
independent of the executor's check_positions message flow, so it can't be
mislabeled or missed. A naked leveraged perp is account-threatening.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

import bot.core.proactive_monitor as pm
from bot.core.proactive_monitor import ProactiveMonitor
from bot.compat import UTC
from datetime import datetime


@pytest.fixture()
def live(monkeypatch):
    monkeypatch.setattr(type(pm.CONFIG), "is_live", lambda self: True)


def _pos(**kw):
    base = dict(symbol="ANIME/USDT", direction="LONG", trade_id="t1",
                status="open", sl_order_id=None, unprotected=False,
                stop_loss=0.002786,
                opened_at=datetime.now(UTC) - timedelta(minutes=10))
    base.update(kw)
    return SimpleNamespace(**base)


def _mon(positions):
    ex = SimpleNamespace(open_positions=list(positions))
    engine = SimpleNamespace(_all_live_executors=lambda: [ex], live_executor=ex)
    return ProactiveMonitor(engine)


class TestAlerts:
    def test_alerts_on_naked_position_past_grace(self, live):
        a = _mon([_pos()])._check_unprotected_positions()
        assert len(a) == 1
        assert a[0].alert_type == "POSITION_UNPROTECTED"
        assert a[0].severity == "CRITICAL"
        assert "ANIME/USDT" in a[0].title

    def test_alerts_when_marked_unprotected(self, live):
        # Has an SL id but explicitly flagged unprotected (stale/failed) → alert.
        a = _mon([_pos(sl_order_id="123", unprotected=True)])._check_unprotected_positions()
        assert len(a) == 1

    def test_protected_position_is_quiet(self, live):
        assert _mon([_pos(sl_order_id="abc123")])._check_unprotected_positions() == []

    def test_within_grace_is_quiet(self, live):
        # Just opened (10s) with no SL yet → still in placement grace, no alarm.
        young = _pos(opened_at=datetime.now(UTC) - timedelta(seconds=10))
        assert _mon([young])._check_unprotected_positions() == []

    def test_non_open_status_skipped(self, live):
        assert _mon([_pos(status="pending_fill")])._check_unprotected_positions() == []

    def test_only_offending_position_alerts(self, live):
        a = _mon([
            _pos(symbol="ANIME/USDT", trade_id="t1"),               # naked → alert
            _pos(symbol="BTC/USDT", trade_id="t2", sl_order_id="x"),  # protected
        ])._check_unprotected_positions()
        assert len(a) == 1 and "ANIME/USDT" in a[0].title


class TestGating:
    def test_quiet_when_not_live(self):
        # No live fixture → is_live False → no alert even if naked.
        assert _mon([_pos()])._check_unprotected_positions() == []

    def test_no_executor_is_quiet(self, live):
        mon = ProactiveMonitor(SimpleNamespace())
        assert mon._check_unprotected_positions() == []
