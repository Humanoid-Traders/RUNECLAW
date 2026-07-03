"""Silent-strangle watchdog: alert when ideas flow but nothing is approved.

The loss-streak latch ran a production backtest dry for ~8 months with zero
operator-visible signal — the bot scans, generates ideas, and rejects every
one. The watchdog diffs the risk engine's cumulative evaluated/approved
counters over a rolling window and WARNs, naming the top rejecting gate.
"""

import time
from types import SimpleNamespace

from bot.core.proactive_monitor import ProactiveMonitor


def _mk_risk(evaluated, approved, fails=None, streak=None):
    return SimpleNamespace(
        eval_stats=lambda: {"evaluated": evaluated, "approved": approved,
                            "last_approval_time": None},
        gate_stats=lambda: {k: {"passed": 0, "failed": v, "skipped": 0}
                            for k, v in (fails or {}).items()},
        streak_state=lambda: streak or {"latched": False},
    )


def _monitor(risk):
    return ProactiveMonitor(SimpleNamespace(risk=risk))


def _seed(monitor, ts, evaluated, approved, fails=None):
    monitor._strangle_snaps.append(
        (ts, evaluated, approved,
         {k: v for k, v in (fails or {}).items()}))


class TestStrangleWatchdog:
    def test_alert_when_ideas_flow_but_zero_approved(self):
        risk = _mk_risk(evaluated=150, approved=5,
                        fails={"LOSS_STREAK": 120, "CONFIDENCE": 30},
                        streak={"latched": True, "consecutive_losses": 3,
                                "soft_limit": 3, "probe_in_seconds": 7200.0})
        mon = _monitor(risk)
        # A window-old snapshot: since then 40 ideas evaluated, 0 approved,
        # LOSS_STREAK rejected 40 more of them.
        _seed(mon, time.time() - 13 * 3600, 110, 5,
              {"LOSS_STREAK": 80, "CONFIDENCE": 30})
        alerts = mon._check_signal_strangle()
        assert len(alerts) == 1
        a = alerts[0]
        assert a.alert_type == "SIGNAL_STRANGLE"
        assert "LOSS_STREAK" in a.body
        assert "zero approved" in a.body
        assert "probe trade in" in a.body and "2.0h" in a.body

    def test_no_alert_when_approvals_happen(self):
        risk = _mk_risk(evaluated=150, approved=8, fails={"CONFIDENCE": 100})
        mon = _monitor(risk)
        _seed(mon, time.time() - 13 * 3600, 110, 5, {"CONFIDENCE": 80})
        assert mon._check_signal_strangle() == []

    def test_no_alert_below_min_ideas(self):
        # Quiet market: only 3 ideas all window — not a strangle.
        risk = _mk_risk(evaluated=113, approved=5, fails={"CONFIDENCE": 100})
        mon = _monitor(risk)
        _seed(mon, time.time() - 13 * 3600, 110, 5, {"CONFIDENCE": 98})
        assert mon._check_signal_strangle() == []

    def test_no_alert_without_window_old_baseline(self):
        # Fresh process: no snapshot is a full window old yet.
        risk = _mk_risk(evaluated=150, approved=0, fails={"CONFIDENCE": 150})
        mon = _monitor(risk)
        assert mon._check_signal_strangle() == []

    def test_realerts_once_per_window_not_per_tick(self):
        risk = _mk_risk(evaluated=150, approved=5, fails={"LOSS_STREAK": 100})
        mon = _monitor(risk)
        _seed(mon, time.time() - 13 * 3600, 110, 5, {"LOSS_STREAK": 60})
        assert len(mon._check_signal_strangle()) == 1
        assert mon._check_signal_strangle() == []   # suppressed

    def test_snapshots_pruned_to_two_windows(self):
        risk = _mk_risk(evaluated=100, approved=10, fails={})
        mon = _monitor(risk)
        _seed(mon, time.time() - 100 * 3600, 1, 0)
        mon._check_signal_strangle()
        assert all(time.time() - s[0] <= 25 * 3600
                   for s in mon._strangle_snaps)


class TestRiskEngineCounters:
    def test_eval_and_approval_counters_accumulate(self):
        import os
        import tempfile
        from datetime import datetime

        from bot.compat import UTC
        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine
        from bot.utils.models import Direction, TradeIdea

        state = os.path.join(tempfile.mkdtemp(prefix="rc-flow-"), "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG, entry_price=100.0,
            stop_loss=95.0, take_profit=110.0, confidence=0.9,
            reasoning="flow", source="scan", timestamp=datetime.now(UTC))
        eng.evaluate(idea)
        eng.evaluate(idea)
        s = eng.eval_stats()
        assert s["evaluated"] == 2
        # Approved or not depends on ambient gates; the invariant is
        # approved <= evaluated and last_approval_time set iff approved > 0.
        assert 0 <= s["approved"] <= 2
        assert (s["last_approval_time"] is not None) == (s["approved"] > 0)

    def test_streak_state_shape(self):
        import os
        import tempfile

        from bot.risk.portfolio import PortfolioTracker
        from bot.risk.risk_engine import RiskEngine

        state = os.path.join(tempfile.mkdtemp(prefix="rc-ss-"), "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
        ss = eng.streak_state()
        assert ss["latched"] is False
        assert ss["probe_in_seconds"] is None
        assert ss["soft_limit"] == 3 and ss["hard_limit"] == 5
        for _ in range(3):
            eng.record_trade_result(-10.0)
        ss = eng.streak_state()
        assert ss["latched"] is True
        assert ss["probe_in_seconds"] is not None and ss["probe_in_seconds"] > 0
