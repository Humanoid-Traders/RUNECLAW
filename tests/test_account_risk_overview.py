"""
Per-user observability — account_risk_overview().

Admin read-only snapshot of live risk PER ACCOUNT (operator + every per-user
account): equity, open positions, margin exposure, and circuit-breaker state.
This is what /accounts surfaces that the /users registration roster does not.
Reading the overview never creates risk state as a side effect, and is fail-open
per account.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.core.engine import RuneClawEngine


class _FakeExec:
    def __init__(self, user_id=None, positions=()):
        self.user_id = user_id
        self.open_positions = list(positions)


class _BadExec:
    def __init__(self, user_id=None):
        self.user_id = user_id

    @property
    def open_positions(self):
        raise RuntimeError("exec boom")


def _pos(cost):
    return SimpleNamespace(cost_usd=cost)


def _risk(circuit=False, streak=0):
    return SimpleNamespace(circuit_breaker_active=circuit, consecutive_losses=streak)


def _engine(operator, user_execs=None, shared_risk=None, user_risk=None,
            op_balance=None, user_balances=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = operator
    eng._user_executors = dict(user_execs or {})
    eng.risk = shared_risk if shared_risk is not None else _risk()
    eng._user_risk = dict(user_risk or {})
    eng.get_live_equity = AsyncMock(return_value=op_balance)
    ub = dict(user_balances or {})
    eng.get_user_live_equity = AsyncMock(side_effect=lambda uid: ub.get(str(uid)))
    return eng


class TestAccountRiskOverview:
    async def test_operator_only_default(self):
        eng = _engine(
            _FakeExec(positions=[_pos(50.0), _pos(100.0)]),
            shared_risk=_risk(circuit=False, streak=1),
            op_balance={"total": 10_000.0},
        )
        rows = await eng.account_risk_overview()
        assert len(rows) == 1
        r = rows[0]
        assert r["account"] == "operator"
        assert r["equity_usd"] == 10_000.0
        assert r["open_positions"] == 2
        assert r["exposure_usd"] == 150.0
        assert r["circuit_open"] is False
        assert r["consecutive_losses"] == 1

    async def test_multi_account_with_per_user_state(self):
        op = _FakeExec(positions=[_pos(20.0)])
        alice = _FakeExec(user_id="alice", positions=[_pos(10.0), _pos(5.0)])
        bob = _FakeExec(user_id="bob", positions=[])
        eng = _engine(
            op, {"alice": alice, "bob": bob},
            shared_risk=_risk(),
            user_risk={"alice": _risk(circuit=True, streak=5)},  # alice halted
            op_balance={"total": 9_000.0},
            user_balances={"alice": {"total": 100.0}, "bob": {"total": 250.0}},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert set(rows) == {"operator", "alice", "bob"}
        assert rows["alice"]["equity_usd"] == 100.0
        assert rows["alice"]["exposure_usd"] == 15.0
        assert rows["alice"]["circuit_open"] is True
        assert rows["alice"]["consecutive_losses"] == 5
        # bob has an executor but no per-user risk engine yet → fresh defaults.
        assert rows["bob"]["equity_usd"] == 250.0
        assert rows["bob"]["open_positions"] == 0
        assert rows["bob"]["circuit_open"] is False
        assert rows["bob"]["consecutive_losses"] == 0

    async def test_no_per_user_engine_does_not_create_one(self):
        alice = _FakeExec(user_id="alice", positions=[])
        eng = _engine(_FakeExec(), {"alice": alice},
                      user_balances={"alice": {"total": 100.0}})
        await eng.account_risk_overview()
        # Overview must be side-effect free — no engine fabricated for alice.
        assert "alice" not in eng._user_risk

    async def test_equity_none_when_unavailable(self):
        eng = _engine(_FakeExec(positions=[]), op_balance=None)
        rows = await eng.account_risk_overview()
        assert rows[0]["equity_usd"] is None

    async def test_fail_open_per_account(self):
        op = _FakeExec(positions=[_pos(30.0)])
        bad = _BadExec(user_id="bad")
        good = _FakeExec(user_id="good", positions=[_pos(40.0)])
        eng = _engine(
            op, {"bad": bad, "good": good},
            op_balance={"total": 5_000.0},
            user_balances={"good": {"total": 500.0}},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["bad"]["error"] is not None
        # The bad account never aborts the sweep.
        assert rows["operator"]["exposure_usd"] == 30.0
        assert rows["good"]["exposure_usd"] == 40.0
        assert rows["good"]["equity_usd"] == 500.0
