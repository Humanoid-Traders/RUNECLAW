"""
/setcap visibility in /accounts.

account_risk_overview() now carries each user's operator-set per-trade margin cap
(cap_usd) so the admin can see configured limits alongside live state — closing
the loop on /setcap (#128), which was previously set-but-invisible. Read-only.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.core.engine import RuneClawEngine


class _FakeExec:
    def __init__(self, user_id=None, positions=()):
        self.user_id = user_id
        self.open_positions = list(positions)


class _FakeStore:
    def __init__(self, caps=None, raises=False):
        self._caps = dict(caps or {})
        self._raises = raises

    def max_margin(self, uid):
        if self._raises:
            raise RuntimeError("store boom")
        return self._caps.get(str(uid))


def _engine(store=None, user_execs=None):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = _FakeExec(positions=[SimpleNamespace(cost_usd=10.0)])
    eng._user_executors = dict(user_execs or {})
    eng.risk = SimpleNamespace(circuit_breaker_active=False, consecutive_losses=0,
                               live_performance_state=lambda: None)
    eng._user_risk = {}
    eng._user_store = store
    eng.get_live_equity = AsyncMock(return_value={"total": 1000.0})
    eng.get_user_live_equity = AsyncMock(return_value={"total": 100.0})
    return eng


class TestAccountsCapDisplay:
    async def test_cap_surfaced_for_capped_user(self):
        eng = _engine(
            store=_FakeStore({"alice": 25.0}),
            user_execs={"alice": _FakeExec(user_id="alice", positions=[])},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["alice"]["cap_usd"] == 25.0
        # Operator row never carries a per-user cap.
        assert rows["operator"]["cap_usd"] is None

    async def test_uncapped_user_is_none(self):
        eng = _engine(
            store=_FakeStore({}),  # no cap set
            user_execs={"bob": _FakeExec(user_id="bob", positions=[])},
        )
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["bob"]["cap_usd"] is None

    async def test_no_store_is_none(self):
        eng = _engine(store=None,
                      user_execs={"carol": _FakeExec(user_id="carol", positions=[])})
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        assert rows["carol"]["cap_usd"] is None

    async def test_store_error_is_fail_open(self):
        eng = _engine(store=_FakeStore(raises=True),
                      user_execs={"dave": _FakeExec(user_id="dave", positions=[])})
        rows = {r["account"]: r for r in await eng.account_risk_overview()}
        # The error doesn't break the row — cap just reads None.
        assert rows["dave"]["cap_usd"] is None
        assert rows["dave"]["error"] is None
