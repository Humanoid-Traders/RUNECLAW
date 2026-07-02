"""Cooldown-after-loss must elapse in SIMULATED time under backtest replay.

A backtest replays months of bars in seconds of wall-clock, so a wall-clock
cooldown (120s default) armed by the first losing trade would silently block
every subsequent idea for thousands of bars — the "2 trades then 7 months of
silence" failure mode. The fix: backtests pin RiskEngine's clock to the
replayed bar time (set_sim_time) and the cooldown check measures elapsed
seconds against the evaluate() call's as_of bar timestamp. Live behavior
(no sim time, no as_of) stays wall-clock.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-cooldown-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea() -> TradeIdea:
    return TradeIdea(
        asset="BTC/USDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=110.0,
        confidence=0.9,
        reasoning="cooldown regression",
        source="scan",
        timestamp=datetime.now(UTC),
    )


def _cooldown_failures(chk) -> list[str]:
    return [f for f in chk.checks_failed if f.startswith("COOLDOWN")]


class TestCooldownSimTime:
    def test_cooldown_elapses_in_bar_time_not_wall_clock(self):
        eng = _engine()
        t0 = datetime(2025, 6, 1, tzinfo=UTC)
        eng.set_sim_time(t0)
        eng.record_trade_result(-50.0)  # loss stamped at simulated t0
        cd = CONFIG.risk.cooldown_after_loss_seconds

        # One simulated second after the loss: cooldown must bind.
        chk = eng.evaluate(_idea(), as_of=t0 + timedelta(seconds=1))
        assert _cooldown_failures(chk), chk.checks_passed

        # One simulated second PAST the window — wall-clock elapsed is ~0s,
        # but bar time has moved on, so the cooldown must be over.
        chk2 = eng.evaluate(_idea(), as_of=t0 + timedelta(seconds=cd + 1))
        assert not _cooldown_failures(chk2), chk2.checks_failed

    def test_loss_at_late_bar_still_binds_at_next_bar(self):
        # Losses stamped mid-replay must arm the cooldown relative to THAT
        # bar, not the epoch: an idea a few sim-seconds later is rejected.
        eng = _engine()
        t0 = datetime(2025, 6, 1, tzinfo=UTC)
        eng.set_sim_time(t0 + timedelta(days=90))
        eng.record_trade_result(-10.0)
        chk = eng.evaluate(
            _idea(), as_of=t0 + timedelta(days=90, seconds=30))
        assert _cooldown_failures(chk)

    def test_live_wall_clock_path_unchanged(self):
        # No sim time, no as_of: the loss is stamped with wall-clock time and
        # an immediate evaluate() sits inside the 120s window.
        eng = _engine()
        eng.record_trade_result(-50.0)
        chk = eng.evaluate(_idea())
        assert _cooldown_failures(chk)
