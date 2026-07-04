"""The drawdown circuit breaker must clear once equity RECOVERS.

Audit bug: the drawdown gate (and the /resume re-trip warning) keyed off
state.max_drawdown_pct — the MONOTONIC worst-ever drawdown, which never falls.
So a single deep dip latched the breaker forever: it re-tripped on every
evaluation and a manual /resume could never stick ("still paused after reset").
The gate now reads CURRENT drawdown (live peak-vs-current equity), which
recovers as the account climbs back, while the account-protection intent is
preserved (it still trips while genuinely underwater).
"""
from __future__ import annotations

import os
import tempfile

from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


def _engine(balance: float = 10_000.0) -> RiskEngine:
    state = os.path.join(tempfile.mkdtemp(prefix="rc-ddrec-"), "risk_state.json")
    return RiskEngine(PortfolioTracker(initial_balance=balance), state_file=state)


def _set_drawdown(p: PortfolioTracker, peak: float, current_dd_pct: float) -> None:
    """Force a peak/current-equity split so snapshot() reports current_dd_pct."""
    p._peak_equity = peak
    p.balance = peak * (1.0 - current_dd_pct / 100.0)


class TestSnapshotSurfacesCurrentDrawdown:
    def test_current_and_max_drawdown_are_distinct(self):
        eng = _engine()
        p = eng._portfolio
        p._max_drawdown_ever = 40.0          # deep historical dip, since recovered
        _set_drawdown(p, peak=20_000.0, current_dd_pct=8.0)
        snap = p.snapshot()
        assert snap.max_drawdown_pct >= 40.0         # monotonic worst-ever
        assert abs(snap.current_drawdown_pct - 8.0) < 0.5  # live, recovered


class TestBreakerRecovers:
    def test_recovered_account_does_not_report_pending_retrip(self):
        eng = _engine()
        limit = eng._effective_max_drawdown_pct()
        p = eng._portfolio
        p._max_drawdown_ever = limit + 30.0          # historical still way over
        _set_drawdown(p, peak=20_000.0, current_dd_pct=max(limit - 5.0, 0.0))
        snap = p.snapshot()
        assert snap.max_drawdown_pct >= limit         # old gate would still fire
        assert snap.current_drawdown_pct < limit      # new gate sees recovery
        # The fix: no drawdown re-trip once the live drawdown is back under limit.
        assert eng.pending_retrip_reason() is None

    def test_still_underwater_account_reports_pending_retrip(self):
        eng = _engine()
        limit = eng._effective_max_drawdown_pct()
        p = eng._portfolio
        _set_drawdown(p, peak=20_000.0, current_dd_pct=limit + 5.0)
        reason = eng.pending_retrip_reason()
        assert reason is not None
        assert "drawdown" in reason
