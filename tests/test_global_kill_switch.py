"""
Global kill-switch across all accounts.

The admin kill-switch (`/closeall`, the emergency-stop confirm, `/halt`) used to
halt only the SHARED risk engine and flatten only the OPERATOR account. Under
per-user live trading that left every user's risk engine un-halted and every
user's open positions un-flattened. These engine-level helpers cover EVERY
account + engine:

  - flatten_all_positions   → close positions on operator + every per-user account
  - emergency_halt_all      → halt all engines + clear queued ideas + flatten all
  - reset_circuit_breaker_all → resume: reset all engines

Default (per-user OFF) → just the operator account + shared engine, i.e.
byte-equivalent to the prior behaviour.
"""

from unittest.mock import patch

from bot.core.engine import RuneClawEngine


class _FakeRisk:
    def __init__(self, raises=False):
        self.halted = None
        self.reset_called = False
        self._raises = raises

    def emergency_halt(self, reason):
        if self._raises:
            raise RuntimeError("halt boom")
        self.halted = reason

    def reset_circuit_breaker(self):
        if self._raises:
            raise RuntimeError("reset boom")
        self.reset_called = True


class _FakeExec:
    def __init__(self, user_id=None, raises=False):
        self.user_id = user_id
        self._raises = raises
        self.closed_reason = None

    async def close_all_positions(self, reason="emergency"):
        if self._raises:
            raise RuntimeError("close boom")
        self.closed_reason = reason
        return [f"closed pos ({reason})"]


def _cfg(live=True):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.is_live.return_value = live
    return p


def _engine(operator, user_execs=None, shared_risk=None, user_risk=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = operator
    eng._user_executors = dict(user_execs or {})
    eng.risk = shared_risk if shared_risk is not None else _FakeRisk()
    eng._user_risk = dict(user_risk or {})
    eng._pending_ideas = {"t1": object(), "t2": object()}
    eng._pending_atr = {"t1": 1.0}
    eng._pending_timing = {"t1": (True, "")}
    eng._pending_pyramid = {"t1": True}
    return eng


# ── flatten_all_positions ───────────────────────────────────────────

class TestFlattenAll:
    async def test_paper_mode_is_noop(self):
        p = _cfg(live=False)
        eng = _engine(_FakeExec())
        try:
            assert await eng.flatten_all_positions() == []
        finally:
            p.stop()

    async def test_flattens_operator_and_all_users(self):
        p = _cfg(live=True)
        op = _FakeExec()
        alice = _FakeExec(user_id="alice")
        bob = _FakeExec(user_id="bob")
        eng = _engine(op, {"alice": alice, "bob": bob})
        try:
            res = await eng.flatten_all_positions(reason="admin_closeall")
            labels = {r["account"] for r in res}
            assert labels == {"operator", "alice", "bob"}
            assert op.closed_reason == "admin_closeall"
            assert alice.closed_reason == "admin_closeall"
            assert bob.closed_reason == "admin_closeall"
        finally:
            p.stop()

    async def test_one_account_error_does_not_block_others(self):
        p = _cfg(live=True)
        op = _FakeExec()
        bad = _FakeExec(user_id="bad", raises=True)
        good = _FakeExec(user_id="good")
        eng = _engine(op, {"bad": bad, "good": good})
        try:
            res = await eng.flatten_all_positions()
            by = {r["account"]: r["messages"] for r in res}
            assert "failed" in by["bad"][0].lower()
            assert good.closed_reason is not None         # still processed
            assert op.closed_reason is not None
        finally:
            p.stop()


# ── emergency_halt_all ──────────────────────────────────────────────

class TestEmergencyHaltAll:
    async def test_halts_all_engines_clears_ideas_flattens(self):
        p = _cfg(live=True)
        shared = _FakeRisk()
        ua, ub = _FakeRisk(), _FakeRisk()
        op = _FakeExec()
        alice = _FakeExec(user_id="alice")
        eng = _engine(op, {"alice": alice}, shared_risk=shared,
                      user_risk={"alice": ua, "bob": ub})
        try:
            summary = await eng.emergency_halt_all("kill")
            assert summary["engines_halted"] == 3          # shared + 2 user engines
            assert shared.halted == "kill" and ua.halted == "kill" and ub.halted == "kill"
            assert summary["pending_cleared"] == 2
            assert eng._pending_ideas == {} and eng._pending_atr == {} and eng._pending_pyramid == {}
            assert eng._pending_timing == {}   # emergency halt drops pending timing state too
            assert {a["account"] for a in summary["accounts"]} == {"operator", "alice"}
            assert op.closed_reason == "kill" and alice.closed_reason == "kill"
        finally:
            p.stop()

    async def test_default_per_user_off_is_operator_only(self):
        p = _cfg(live=True)
        shared = _FakeRisk()
        op = _FakeExec()
        eng = _engine(op, {}, shared_risk=shared, user_risk={})
        try:
            summary = await eng.emergency_halt_all("kill")
            assert summary["engines_halted"] == 1
            assert [a["account"] for a in summary["accounts"]] == ["operator"]
            assert shared.halted == "kill"
        finally:
            p.stop()

    async def test_engine_halt_error_is_fail_open(self):
        p = _cfg(live=True)
        shared = _FakeRisk()
        bad = _FakeRisk(raises=True)
        good = _FakeRisk()
        eng = _engine(_FakeExec(), {}, shared_risk=shared,
                      user_risk={"bad": bad, "good": good})
        try:
            summary = await eng.emergency_halt_all("kill")
            # shared + good halt; bad raised but did not abort the sweep.
            assert summary["engines_halted"] == 2
            assert good.halted == "kill"
        finally:
            p.stop()

    async def test_paper_mode_halts_engines_without_flatten(self):
        p = _cfg(live=False)
        shared = _FakeRisk()
        eng = _engine(_FakeExec(), {}, shared_risk=shared, user_risk={})
        try:
            summary = await eng.emergency_halt_all("kill")
            assert summary["engines_halted"] == 1
            assert summary["accounts"] == []              # no flatten in paper
            assert shared.halted == "kill"
        finally:
            p.stop()


# ── reset_circuit_breaker_all ───────────────────────────────────────

class TestResetAll:
    async def test_resets_shared_and_all_users(self):
        shared = _FakeRisk()
        ua, ub = _FakeRisk(), _FakeRisk()
        eng = _engine(_FakeExec(), shared_risk=shared, user_risk={"a": ua, "b": ub})
        assert eng.reset_circuit_breaker_all() == 3
        assert shared.reset_called and ua.reset_called and ub.reset_called

    async def test_reset_error_is_fail_open(self):
        shared = _FakeRisk()
        bad = _FakeRisk(raises=True)
        good = _FakeRisk()
        eng = _engine(_FakeExec(), shared_risk=shared, user_risk={"bad": bad, "good": good})
        assert eng.reset_circuit_breaker_all() == 2        # shared + good
        assert good.reset_called
