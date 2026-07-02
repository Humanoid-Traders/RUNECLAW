"""Half-open recovery for the soft loss-streak gate (risk check #9).

The soft gate rejects new ideas at (hard - 2) consecutive losses. The streak
only decays on a WIN — with trading blocked no win can ever happen, so the
gate was a PERMANENT silent latch two losses below the visible circuit
breaker. Production-data backtest: 3 early losses froze the strategy for the
remaining ~8 months of bars; live freezes identically, the operator just sees
a bot that scans but never trades.

Recovery contract: after LOSS_STREAK_PROBE_HOURS since the last loss, ONE
probe trade is allowed at a time (only while flat). A losing probe re-arms
the gate; winning probes decay the streak. Unknown last-loss time fails
closed. Probe hours 0 restores the legacy latch.
"""

import dataclasses
import os
import tempfile
from datetime import datetime, timedelta

import pytest

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-probe-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=0.9,
        reasoning="probe", source="scan", timestamp=T0)


def _streak_reason(check):
    return next((r for r in check.checks_failed + check.checks_passed
                 if r.startswith("LOSS_STREAK")), "")


def _arm(eng, losses=3, at=T0):
    """Record `losses` consecutive losses at simulated time `at`."""
    eng.set_sim_time(at)
    for _ in range(losses):
        eng.record_trade_result(-10.0)


class TestSoftGateLatch:
    def test_latched_within_cooloff(self):
        eng = _engine()
        _arm(eng)
        eng.set_sim_time(T0 + timedelta(hours=1))
        check = eng.evaluate(_idea())
        assert "consecutive losses" in _streak_reason(check)

    def test_probe_allowed_after_cooloff(self):
        eng = _engine()
        _arm(eng)
        eng.set_sim_time(T0 + timedelta(hours=25))
        check = eng.evaluate(_idea())
        assert "consecutive losses" not in _streak_reason(check)
        assert "probe allowed" in _streak_reason(check)

    def test_losing_probe_rearms_gate(self):
        eng = _engine()
        _arm(eng)
        t1 = T0 + timedelta(hours=25)
        eng.set_sim_time(t1)
        assert "probe allowed" in _streak_reason(eng.evaluate(_idea()))
        eng.record_trade_result(-10.0)   # the probe loses
        eng.set_sim_time(t1 + timedelta(hours=1))
        assert "consecutive losses" in _streak_reason(eng.evaluate(_idea()))
        # ...and re-opens after another full cool-off.
        eng.set_sim_time(t1 + timedelta(hours=25))
        assert "probe allowed" in _streak_reason(eng.evaluate(_idea()))

    def test_winning_probes_decay_streak_back_to_normal(self):
        eng = _engine()
        _arm(eng, losses=3)
        eng.set_sim_time(T0 + timedelta(hours=25))
        eng.record_trade_result(+20.0)   # probe wins -> streak 2 (< soft 3)
        check = eng.evaluate(_idea())
        assert "OK" in _streak_reason(check)

    def test_probe_blocked_while_position_open(self):
        # Half-open means ONE probe at a time: with a position already open
        # the gate stays shut even after the cool-off.
        eng = _engine()
        _arm(eng)
        eng._portfolio.open_position(_idea(), 100.0)
        eng.set_sim_time(T0 + timedelta(hours=25))
        assert "consecutive losses" in _streak_reason(eng.evaluate(_idea()))

    def test_probe_hours_zero_restores_legacy_latch(self, monkeypatch):
        new_risk = dataclasses.replace(CONFIG.risk, loss_streak_probe_hours=0.0)
        monkeypatch.setattr("bot.risk.risk_engine.CONFIG",
                            dataclasses.replace(CONFIG, risk=new_risk))
        eng = _engine()
        _arm(eng)
        eng.set_sim_time(T0 + timedelta(days=300))
        assert "consecutive losses" in _streak_reason(eng.evaluate(_idea()))

    def test_unknown_last_loss_time_fails_closed(self):
        eng = _engine()
        eng._consecutive_losses = 3      # restored state without a timestamp
        eng._last_loss_time = None
        eng.set_sim_time(T0 + timedelta(days=30))
        assert "consecutive losses" in _streak_reason(eng.evaluate(_idea()))

    def test_default_probe_hours(self):
        assert CONFIG.risk.loss_streak_probe_hours == pytest.approx(24.0)
