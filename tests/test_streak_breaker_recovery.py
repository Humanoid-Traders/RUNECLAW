"""Streak-breaker self-recovery: an unattended live bot must not latch PAUSED.

The consecutive-loss circuit breaker is manual-reset only, so a live account
sits paused until a human runs /resume and re-pauses on the next streak. When
STREAK_BREAKER_AUTORESET_HOURS > 0 the streak breaker auto-clears after that
cool-off since the last loss — but daily-loss / drawdown trips keep their own
paths (account-drain protection intact).
"""

import dataclasses
import os
import tempfile
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea

T0 = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-sbr-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=0.9,
        reasoning="x", source="scan", timestamp=datetime.now(UTC))


def _trip_streak(eng, at=T0):
    eng.set_sim_time(at)
    for _ in range(CONFIG.risk.max_consecutive_losses):
        eng.record_trade_result(-10.0)
    assert eng.circuit_breaker_active


def _with_hours(monkeypatch, hours):
    new_risk = dataclasses.replace(CONFIG.risk,
                                   streak_breaker_autoreset_hours=hours)
    monkeypatch.setattr("bot.risk.risk_engine.CONFIG",
                        dataclasses.replace(CONFIG, risk=new_risk))


class TestStreakBreakerRecovery:
    def test_default_off_stays_paused(self, monkeypatch):
        _with_hours(monkeypatch, 0.0)
        eng = _engine()
        _trip_streak(eng)
        eng.set_sim_time(T0 + timedelta(days=30))
        eng.evaluate(_idea(), atr=2.0)
        assert eng.circuit_breaker_active   # manual-only, never self-clears

    def test_recovers_after_cooloff(self, monkeypatch):
        _with_hours(monkeypatch, 6.0)
        eng = _engine()
        _trip_streak(eng)
        eng.set_sim_time(T0 + timedelta(hours=2))
        eng.evaluate(_idea(), atr=2.0)
        assert eng.circuit_breaker_active   # too soon
        eng.set_sim_time(T0 + timedelta(hours=7))
        eng.evaluate(_idea(), atr=2.0)
        assert not eng.circuit_breaker_active
        assert eng.consecutive_losses == 0  # streak zeroed like /resume

    def test_drawdown_trip_never_streak_recovers(self, monkeypatch):
        _with_hours(monkeypatch, 6.0)
        eng = _engine()
        eng.set_sim_time(T0)
        eng._trip_circuit_breaker("max drawdown breached", cause="drawdown")
        eng._last_loss_time = T0.timestamp()
        eng.set_sim_time(T0 + timedelta(hours=30))
        eng.evaluate(_idea(), atr=2.0)
        # A drawdown-caused pause is account protection — the streak
        # auto-recovery must NOT clear it.
        assert eng.circuit_breaker_active

    def test_default_config_is_off(self):
        assert CONFIG.risk.streak_breaker_autoreset_hours == 0.0
