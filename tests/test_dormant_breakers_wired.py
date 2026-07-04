"""Wire up the three previously-inert risk mechanisms the audit flagged.

- Bug 20 (always on): the warning-rate breaker only cleared when the SAME key
  fired again, so a burst that simply STOPPED latched it forever. A time-based
  refresh on every evaluate() now clears it once the rate genuinely subsides.
- Bug 9 (opt-in): record_equity_snapshot() was never called, leaving the
  equity-curve breaker inert. evaluate() now feeds it when enabled.
- Bug 21 (opt-in): check_drawdown_recovery() was never called, leaving recovery
  mode inert. evaluate() now drives it from the live drawdown when enabled.

9/21 ADD restrictions (pause / de-risk), so they are gated OFF by default.
"""
from __future__ import annotations

import inspect
import os
import tempfile

from bot.config import CONFIG
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine() -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-dormant-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)


class TestWarningRateAutoClear:
    def test_stopped_burst_auto_clears(self):
        eng = _engine()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "x"
        old = eng._now() - eng._warning_rate_window - 10.0
        eng._warning_events.clear()
        for _ in range(eng._warning_rate_threshold + 5):
            eng._warning_events.append((old, "x"))   # all aged out of the window
        eng._refresh_warning_rate_breaker()
        assert eng._warning_rate_tripped is False

    def test_ongoing_burst_stays_tripped(self):
        eng = _engine()
        eng._warning_rate_tripped = True
        eng._warning_rate_trip_key = "x"
        now = eng._now()
        eng._warning_events.clear()
        for _ in range(eng._warning_rate_threshold + 5):
            eng._warning_events.append((now, "x"))   # still firing in-window
        eng._refresh_warning_rate_breaker()
        assert eng._warning_rate_tripped is True

    def test_refresh_is_noop_when_not_tripped(self):
        eng = _engine()
        assert eng._warning_rate_tripped is False
        eng._refresh_warning_rate_breaker()
        assert eng._warning_rate_tripped is False


class TestFeedersGatedOffByDefault:
    def test_new_restrictions_default_off(self):
        assert CONFIG.risk.equity_curve_breaker_enabled is False
        assert CONFIG.risk.drawdown_recovery_enabled is False

    def test_evaluate_drives_all_three_feeders(self):
        src = inspect.getsource(RiskEngine._evaluate_locked)
        assert "_refresh_warning_rate_breaker()" in src          # bug 20, unconditional
        assert "equity_curve_breaker_enabled" in src             # bug 9, gated
        assert "record_equity_snapshot(" in src
        assert "drawdown_recovery_enabled" in src                # bug 21, gated
        assert "check_drawdown_recovery(" in src
