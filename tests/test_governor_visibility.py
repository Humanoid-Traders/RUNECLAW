"""
Live-performance governor visibility.

RiskEngine.live_performance_state() is a read-only diagnostic snapshot
(enabled / samples / win_rate / net_pnl / multiplier / status), and
account_risk_overview() surfaces it per account so an admin can see — in
/accounts — when and why the governor is throttling an account's sizing.
"""

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.risk.risk_engine import RiskEngine
from bot.core.engine import RuneClawEngine


# ── live_performance_state() ────────────────────────────────────────

def _eng(pnls):
    e = RiskEngine.__new__(RiskEngine)
    e._realized_pnl_window = deque(pnls)
    return e


def _cfg(enabled=True, window=20, min_samples=10,
         reduce_wr=0.40, pause_wr=0.25, reduce_mult=0.5):
    p = patch("bot.risk.risk_engine.CONFIG")
    m = p.start()
    m.risk.live_performance_governor_enabled = enabled
    m.risk.live_perf_window = window
    m.risk.live_perf_min_samples = min_samples
    m.risk.live_perf_reduce_winrate = reduce_wr
    m.risk.live_perf_pause_winrate = pause_wr
    m.risk.live_perf_reduce_mult = reduce_mult
    return p


class TestLivePerformanceState:
    def test_off_when_disabled(self):
        p = _cfg(enabled=False)
        try:
            s = _eng([1.0, -1.0] * 10).live_performance_state()
            assert s["status"] == "OFF"
            assert s["enabled"] is False
        finally:
            p.stop()

    def test_warmup_below_min_samples(self):
        p = _cfg(min_samples=10)
        try:
            s = _eng([-1.0] * 5).live_performance_state()
            assert s["status"] == "WARMUP"
            assert s["samples"] == 5
        finally:
            p.stop()

    def test_ok_when_healthy(self):
        p = _cfg()
        try:
            s = _eng([10.0] * 6 + [-5.0] * 4).live_performance_state()
            assert s["status"] == "OK"
            assert s["multiplier"] == 1.0
            assert s["win_rate"] == 0.6
            assert s["net_pnl"] == 40.0
            assert s["samples"] == 10
        finally:
            p.stop()

    def test_reduce_status(self):
        p = _cfg(reduce_mult=0.5)
        try:
            # win rate 0.3 (<=0.40), net positive → REDUCE
            s = _eng([100.0] * 3 + [-1.0] * 7).live_performance_state()
            assert s["status"] == "REDUCE"
            assert s["multiplier"] == 0.5
        finally:
            p.stop()

    def test_pause_status(self):
        p = _cfg()
        try:
            # win rate 0.2 (<=0.25) AND net negative → PAUSE
            s = _eng([1.0] * 2 + [-10.0] * 8).live_performance_state()
            assert s["status"] == "PAUSE"
            assert s["multiplier"] == 0.0
        finally:
            p.stop()

    def test_fail_safe_on_bad_window(self):
        p = _cfg()
        try:
            e = RiskEngine.__new__(RiskEngine)
            e._realized_pnl_window = None  # list(None) raises
            s = e.live_performance_state()
            assert s["status"] == "OFF" and s["multiplier"] == 1.0
        finally:
            p.stop()


# ── account_risk_overview() surfaces governor ───────────────────────

class _FakeExec:
    def __init__(self, user_id=None, positions=()):
        self.user_id = user_id
        self.open_positions = list(positions)


class _FakeRisk:
    def __init__(self, gov_status="OK"):
        self.circuit_breaker_active = False
        self.consecutive_losses = 0
        self._gov = gov_status

    def live_performance_state(self):
        return {"enabled": True, "samples": 12, "win_rate": 0.3, "net_pnl": -20.0,
                "multiplier": 0.5 if self._gov == "REDUCE" else 1.0,
                "status": self._gov}


def _overview_engine(shared_gov="OK"):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = _FakeExec(positions=[SimpleNamespace(cost_usd=10.0)])
    eng._user_executors = {}
    eng.risk = _FakeRisk(gov_status=shared_gov)
    eng._user_risk = {}
    eng.get_live_equity = AsyncMock(return_value={"total": 1000.0})
    eng.get_user_live_equity = AsyncMock(return_value=None)
    return eng


class TestOverviewGovernor:
    async def test_operator_row_includes_governor_state(self):
        rows = await _overview_engine(shared_gov="REDUCE").account_risk_overview()
        assert rows[0]["governor"]["status"] == "REDUCE"
        assert rows[0]["governor"]["multiplier"] == 0.5

    async def test_ok_governor_still_reported(self):
        rows = await _overview_engine(shared_gov="OK").account_risk_overview()
        assert rows[0]["governor"]["status"] == "OK"
