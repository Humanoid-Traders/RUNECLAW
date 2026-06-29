"""
Per-user live equity for the confirm-time risk re-check.

A regular user's confirmed LIVE trade executes on THEIR OWN linked Bitget
account (per-user live), but the pre-execution risk re-check used to size and
count against the OPERATOR balance cache — so a user with $100 could be sized
against the operator's $10k. get_user_live_equity / _live_recheck_context route
the re-check to the right account. Gated by PER_USER_LIVE_ENABLED (default OFF):
with the flag off — and for operator/admin/auto callers — the path is
byte-identical to the operator behaviour.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.core.engine import RuneClawEngine


# ── Harness ─────────────────────────────────────────────────────────

class _FakeExec:
    def __init__(self, balance=None, positions=(), raises=False):
        self._balance = balance or {}
        self._raises = raises
        self.open_positions = list(positions)

    async def fetch_balance(self):
        if self._raises:
            raise RuntimeError("balance boom")
        return self._balance


def _pos(status="open"):
    return SimpleNamespace(status=status)


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = _FakeExec(balance={"total": 10_000.0})
    eng._live_balance_cache = {"total": 10_000.0}
    eng._user_live_balance_cache = {}
    eng._user_live_balance_cache_ts = {}
    eng._LIVE_BALANCE_TTL = 30.0
    eng._is_operator_user = lambda uid: False
    eng.get_live_equity = AsyncMock(return_value={"total": 10_000.0})
    return eng


def _cfg(live=True, per_user=True):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.is_live.return_value = live
    m.per_user_live_enabled = per_user
    return p


# ── get_user_live_equity ────────────────────────────────────────────

class TestGetUserLiveEquity:
    async def test_paper_mode_returns_none(self):
        p = _cfg(live=False)
        eng = _engine()
        eng._executor_for = lambda uid: eng.live_executor
        try:
            assert await eng.get_user_live_equity("alice") is None
        finally:
            p.stop()

    async def test_per_user_off_uses_operator_balance(self):
        p = _cfg(per_user=False)
        eng = _engine()
        eng._executor_for = lambda uid: eng.live_executor
        try:
            assert await eng.get_user_live_equity("alice") == {"total": 10_000.0}
            eng.get_live_equity.assert_awaited()
        finally:
            p.stop()

    async def test_operator_user_uses_operator_balance(self):
        p = _cfg(per_user=True)
        eng = _engine()
        eng._is_operator_user = lambda uid: True
        eng._executor_for = lambda uid: _FakeExec(balance={"total": 5.0})
        try:
            # Operator/admin trades the operator account → operator balance.
            assert await eng.get_user_live_equity("admin1") == {"total": 10_000.0}
        finally:
            p.stop()

    async def test_no_keys_falls_back_to_operator(self):
        p = _cfg(per_user=True)
        eng = _engine()
        # Executor falls back to the operator (user has no own keys).
        eng._executor_for = lambda uid: eng.live_executor
        try:
            assert await eng.get_user_live_equity("alice") == {"total": 10_000.0}
        finally:
            p.stop()

    async def test_regular_user_uses_own_balance(self):
        p = _cfg(per_user=True)
        eng = _engine()
        user_ex = _FakeExec(balance={"total": 100.0})
        eng._executor_for = lambda uid: user_ex
        try:
            assert await eng.get_user_live_equity("alice") == {"total": 100.0}
            # Cached for next time.
            assert eng._user_live_balance_cache["alice"] == {"total": 100.0}
        finally:
            p.stop()

    async def test_cache_hit_skips_refetch(self):
        p = _cfg(per_user=True)
        eng = _engine()
        user_ex = _FakeExec(balance={"total": 100.0})
        eng._executor_for = lambda uid: user_ex
        try:
            await eng.get_user_live_equity("alice")           # populate
            user_ex._balance = {"total": 999.0}               # would change on refetch
            assert (await eng.get_user_live_equity("alice"))["total"] == 100.0
        finally:
            p.stop()

    async def test_fetch_error_returns_cached(self):
        p = _cfg(per_user=True)
        eng = _engine()
        eng._user_live_balance_cache["alice"] = {"total": 77.0}
        eng._user_live_balance_cache_ts["alice"] = 0.0  # stale → triggers refetch
        eng._executor_for = lambda uid: _FakeExec(raises=True)
        try:
            assert (await eng.get_user_live_equity("alice"))["total"] == 77.0
        finally:
            p.stop()

    async def test_fetch_error_no_cache_returns_none(self):
        p = _cfg(per_user=True)
        eng = _engine()
        eng._executor_for = lambda uid: _FakeExec(raises=True)
        try:
            assert await eng.get_user_live_equity("alice") is None
        finally:
            p.stop()


# ── _live_recheck_context ───────────────────────────────────────────

class TestLiveRecheckContext:
    async def test_paper_mode_returns_none_none(self):
        p = _cfg(live=False)
        eng = _engine()
        eng._executor_for = lambda uid: eng.live_executor
        try:
            assert await eng._live_recheck_context("alice") == (None, None)
        finally:
            p.stop()

    async def test_operator_path_uses_operator_equity_and_count(self):
        p = _cfg(per_user=False)
        eng = _engine()
        eng.live_executor.open_positions = [_pos("pending_fill"), _pos("open")]
        eng._executor_for = lambda uid: eng.live_executor
        with patch("bot.core.engine.get_exchange_position_count",
                   new=AsyncMock(return_value=3)):
            try:
                eq, cnt = await eng._live_recheck_context("")
                assert eq == 10_000.0
                assert cnt == 3 + 1   # exchange count + 1 pending_fill
            finally:
                p.stop()

    async def test_operator_path_falls_back_to_local_count_on_error(self):
        p = _cfg(per_user=False)
        eng = _engine()
        eng.live_executor.open_positions = [_pos("open"), _pos("pending_fill")]
        eng._executor_for = lambda uid: eng.live_executor
        with patch("bot.core.engine.get_exchange_position_count",
                   new=AsyncMock(side_effect=RuntimeError("rpc down"))):
            try:
                eq, cnt = await eng._live_recheck_context("")
                assert eq == 10_000.0
                assert cnt == 2       # len(local open_positions)
            finally:
                p.stop()

    async def test_per_user_path_uses_own_equity_and_count(self):
        p = _cfg(per_user=True)
        eng = _engine()
        user_ex = _FakeExec(balance={"total": 100.0},
                            positions=[_pos("open"), _pos("open")])
        eng._executor_for = lambda uid: user_ex
        eng.get_user_live_equity = AsyncMock(return_value={"total": 100.0})
        try:
            eq, cnt = await eng._live_recheck_context("alice")
            assert eq == 100.0
            assert cnt == 2
        finally:
            p.stop()

    async def test_per_user_none_equity_when_unavailable(self):
        p = _cfg(per_user=True)
        eng = _engine()
        user_ex = _FakeExec(positions=[_pos("open")])
        eng._executor_for = lambda uid: user_ex
        eng.get_user_live_equity = AsyncMock(return_value=None)
        try:
            eq, cnt = await eng._live_recheck_context("alice")
            assert eq is None     # caller falls back to capped paper sizing
            assert cnt == 1
        finally:
            p.stop()
