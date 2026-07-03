"""Liveness: the risk engine must never strangle itself permanently.

Verdict-level guarantee (stronger than the reason-string tests in
test_loss_streak_probe): from a latched loss-streak state, the FULL
evaluation pipeline returns to APPROVED once the probe cool-off elapses —
no manual reset, no win required first. This is the regression test for the
production-data finding where 3 early losses silently halted all trading
for the remaining ~8 months of the backtest.
"""

import os
import tempfile
from datetime import datetime, timedelta

from bot.compat import UTC
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, RiskVerdict, TradeIdea

T0 = datetime(2025, 1, 1, 12, tzinfo=UTC)


def _engine():
    state = os.path.join(tempfile.mkdtemp(prefix="rc-live-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


def _idea():
    # Fresh wall-clock timestamp: STALE_DATA measures idea age in wall time
    # even under a pinned sim clock (only loss timestamps are sim-time).
    return TradeIdea(
        asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
        stop_loss=95.0, take_profit=110.0, confidence=0.9,
        reasoning="liveness", source="scan", timestamp=datetime.now(UTC))


class TestEngineRecoversWithoutHumanIntervention:
    def test_latched_then_probe_approves_at_verdict_level(self):
        eng = _engine()
        eng.set_sim_time(T0)
        for _ in range(3):                      # soft limit (hard 5 - 2)
            eng.record_trade_result(-10.0)

        eng.set_sim_time(T0 + timedelta(hours=1))
        assert eng.evaluate(_idea(), atr=2.0).verdict == RiskVerdict.REJECTED

        eng.set_sim_time(T0 + timedelta(hours=25))
        check = eng.evaluate(_idea(), atr=2.0)
        assert check.verdict == RiskVerdict.APPROVED, check.reason

    def test_probe_cycle_can_reach_hard_breaker_but_only_via_trades(self):
        # Safety is preserved: losing probes still walk the streak to the
        # HARD breaker (manual reset). Liveness never bypasses that stop.
        eng = _engine()
        t = T0
        eng.set_sim_time(t)
        for _ in range(3):
            eng.record_trade_result(-10.0)
        for _ in range(2):                      # two losing probes: 3 -> 5
            t += timedelta(hours=25)
            eng.set_sim_time(t)
            assert eng.evaluate(_idea(), atr=2.0).verdict == RiskVerdict.APPROVED
            eng.record_trade_result(-10.0)
        assert eng.circuit_breaker_active       # hard limit reached
        t += timedelta(hours=25)
        eng.set_sim_time(t)
        # Breaker is a HARD stop: no probe tunnels through it.
        assert eng.evaluate(_idea(), atr=2.0).verdict == RiskVerdict.REJECTED

    def test_winning_probes_fully_restore_flow(self):
        eng = _engine()
        eng.set_sim_time(T0)
        for _ in range(3):
            eng.record_trade_result(-10.0)
        eng.set_sim_time(T0 + timedelta(hours=25))
        eng.record_trade_result(+20.0)          # probe wins: streak 3 -> 2
        check = eng.evaluate(_idea(), atr=2.0)
        assert check.verdict == RiskVerdict.APPROVED
        # No cool-off needed anymore — gate is fully open below the soft limit.
        eng.set_sim_time(T0 + timedelta(hours=25, minutes=30))
        assert eng.evaluate(_idea(), atr=2.0).verdict == RiskVerdict.APPROVED
