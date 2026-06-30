"""
Daily-loss circuit-breaker auto-reset at day rollover (deep-audit medium).

The circuit breaker is a single latch with no record of WHY it tripped, so a
daily-loss trip — a per-DAY guard — stayed halted until a human ran /reset, even
after daily_pnl rolled back to ~0 the next day. The fix tracks the trip cause +
UTC day and, when DAILY_LOSS_BREAKER_AUTORESET is on, clears ONLY a daily-loss
trip once the day has rolled over (drawdown/streak/manual stay manual).
"""

import inspect

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine

_should = RiskEngine._should_autoreset_daily_breaker


def _engine(tmp_path):
    return RiskEngine(PortfolioTracker(initial_balance=1000.0),
                      state_file=str(tmp_path / "risk_state.json"))


# enabled, open, cause, trip_day, today, streak<max all satisfied by default:
def _ok(**kw):
    args = dict(circuit_open=True, cause="daily_loss", trip_day="2026-06-29",
                today="2026-06-30", enabled=True, streak=0, max_streak=3)
    args.update(kw)
    return _should(args["circuit_open"], args["cause"], args["trip_day"],
                   args["today"], args["enabled"], args["streak"], args["max_streak"])


class TestPredicate:
    def test_happy_path_resets(self):
        assert _ok() is True

    def test_disabled_no_reset(self):
        assert _ok(enabled=False) is False

    def test_not_open_no_reset(self):
        assert _ok(circuit_open=False) is False

    def test_drawdown_cause_stays_manual(self):
        assert _ok(cause="drawdown") is False

    def test_streak_cause_stays_manual(self):
        assert _ok(cause="streak") is False

    def test_manual_cause_stays_manual(self):
        assert _ok(cause="manual") is False

    def test_same_day_no_reset(self):
        assert _ok(today="2026-06-29") is False

    def test_empty_trip_day_no_reset(self):
        assert _ok(trip_day="") is False

    def test_active_streak_blocks_reset(self):
        # Never resume into a maxed loss-streak.
        assert _ok(streak=3, max_streak=3) is False


class TestTripRecording:
    def test_daily_loss_trip_records_cause_and_day(self, tmp_path):
        eng = _engine(tmp_path)
        eng._trip_circuit_breaker("daily loss limit breached", cause="daily_loss")
        assert eng._circuit_open is True
        assert eng._circuit_trip_cause == "daily_loss"
        assert len(eng._circuit_trip_day) == 10  # YYYY-MM-DD

    def test_default_cause_is_manual(self, tmp_path):
        eng = _engine(tmp_path)
        eng._trip_circuit_breaker("halt")
        assert eng._circuit_trip_cause == "manual"

    def test_cause_not_overwritten_while_open(self, tmp_path):
        # First (owning) cause wins; a second trip while open does not change it.
        eng = _engine(tmp_path)
        eng._trip_circuit_breaker("drawdown", cause="drawdown")
        eng._trip_circuit_breaker("daily loss", cause="daily_loss")
        assert eng._circuit_trip_cause == "drawdown"


class TestManualResetClears:
    def test_reset_clears_cause_and_day(self, tmp_path):
        eng = _engine(tmp_path)
        eng._trip_circuit_breaker("daily loss", cause="daily_loss")
        eng.reset_circuit_breaker()
        assert eng._circuit_open is False
        assert eng._circuit_trip_cause == ""
        assert eng._circuit_trip_day == ""


class TestPersistence:
    def test_cause_and_day_round_trip(self, tmp_path):
        eng = _engine(tmp_path)
        eng._trip_circuit_breaker("daily loss", cause="daily_loss")
        exported = eng._export_state_dict()
        assert exported["circuit_trip_cause"] == "daily_loss"
        assert exported["circuit_trip_day"] == eng._circuit_trip_day
        # A fresh engine on the same state file restores cause + day.
        eng2 = _engine(tmp_path)
        assert eng2._circuit_trip_cause == "daily_loss"
        assert eng2._circuit_trip_day == eng._circuit_trip_day
        assert eng2._circuit_open is True


class TestWiring:
    def test_evaluate_uses_autoreset_predicate(self):
        # The auto-reset runs inside the locked evaluation impl.
        src = inspect.getsource(RiskEngine._evaluate_locked)
        assert "_should_autoreset_daily_breaker" in src
        assert "AUTO_RESET" in src
