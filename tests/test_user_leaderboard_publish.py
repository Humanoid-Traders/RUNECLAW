"""Engine per-user opt-in leaderboard publishing (community C2).

The consent/privacy invariants under test are hard lines:
- opt-in only; revocable (opt-out reconcile-removes on the next pull);
- the sealed statement's account_ids is the HANDLE — never a telegram id
  (it would be embedded verbatim in the public statement and registry file);
- fills come only from an ALREADY-LIVE per-user executor: the operator's
  account never publishes under a user handle (isolation guard);
- a transport failure never reads as a mass opt-out;
- triple-gated default-OFF.

The engine method is exercised UNBOUND on a stub self — no engine boot, no
network, no disk: publisher class and registry are patched at their modules.
"""
import inspect

import pytest

import bot.core.engine as eng_mod
import bot.proofofpnl.scheduler as sched
import bot.utils.leaderboard_pull as lp
from bot.core.engine import RuneClawEngine


class _FakeExchange:
    async def fetch_my_trades(self, symbol=None, since=None, limit=200):
        return [{"id": "t1"}]


class _FakeExecutor:
    async def _get_exchange(self):
        return _FakeExchange()


class _FakePublisher:
    """Stands in for ProofOfPnLPublisher: records ctor args, always due."""
    built = []

    def __init__(self, *, account_ids, venue="bitget", store=None, **kw):
        self._account_ids = list(account_ids)
        self.venue = venue
        _FakePublisher.built.append(self)

    def due(self, now_ts):
        return True

    def publish(self, now_ts, trades, **kw):
        return {"publish_hash": "h", "account_ids": list(self._account_ids)}


class _FakeRegistry:
    def __init__(self):
        self.put_calls = []
        self.removed = []

    def put(self, handle, pub):
        self.put_calls.append((handle, pub))
        return True

    def remove(self, handle):
        self.removed.append(handle)
        return True


class _Self:
    """Stub engine self with only the attrs the method touches."""

    def __init__(self):
        self.live_executor = object()          # the OPERATOR executor sentinel
        self._user_executors = {}
        self._last_user_lb_pull_ts = 0.0
        self._operator_ids = set()

    def _is_operator_user(self, tg):
        return str(tg) in self._operator_ids


@pytest.fixture(autouse=True)
def _per_user_live_on():
    # CONFIG is a frozen dataclass: flip the instance field directly and ALWAYS
    # restore (never leave global config mutated — 2026-07 full-suite audit).
    orig = getattr(eng_mod.CONFIG, "per_user_live_enabled", False)
    object.__setattr__(eng_mod.CONFIG, "per_user_live_enabled", True)
    yield
    object.__setattr__(eng_mod.CONFIG, "per_user_live_enabled", orig)


def _arm(monkeypatch, optins, registry):
    """Enable the env gates and patch the collaborators."""
    monkeypatch.setenv("PROOFOFPNL_PUBLISH_ENABLED", "1")
    monkeypatch.setenv("PROOFOFPNL_USER_LEADERBOARD_ENABLED", "1")
    monkeypatch.setattr(lp, "fetch_leaderboard_optins", lambda: optins)
    monkeypatch.setattr(sched, "ProofOfPnLPublisher", _FakePublisher)
    import bot.proofofpnl.leaderboard as lb
    monkeypatch.setattr(lb, "get_leaderboard_registry", lambda: registry)
    _FakePublisher.built = []


async def _run(self_stub):
    await RuneClawEngine._maybe_publish_user_leaderboards(self_stub)


@pytest.mark.asyncio
async def test_opted_in_live_user_publishes_under_handle(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, [{"user_id": 5, "telegram_id": "111", "handle": "runefox"}], reg)
    s = _Self()
    s._user_executors["111"] = _FakeExecutor()
    await _run(s)
    assert [h for h, _ in reg.put_calls] == ["runefox"]
    assert s._user_board_handles == {"111": "runefox"}
    # THE privacy invariant: account_ids is the handle, never the telegram id.
    assert _FakePublisher.built[0]._account_ids == ["runefox"]
    assert "111" not in str(_FakePublisher.built[0]._account_ids)


@pytest.mark.asyncio
async def test_user_without_own_executor_is_skipped(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, [{"telegram_id": "111", "handle": "runefox"}], reg)
    s = _Self()                                  # no executor for 111
    await _run(s)
    assert reg.put_calls == []


@pytest.mark.asyncio
async def test_operator_executor_fallback_never_publishes(monkeypatch):
    # ISOLATION GUARD: an entry mapped to the shared operator executor must be
    # skipped — the operator's fills never publish under a user handle.
    reg = _FakeRegistry()
    _arm(monkeypatch, [{"telegram_id": "111", "handle": "runefox"}], reg)
    s = _Self()
    s._user_executors["111"] = s.live_executor
    await _run(s)
    assert reg.put_calls == []


@pytest.mark.asyncio
async def test_operator_user_id_never_publishes(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, [{"telegram_id": "999", "handle": "bosshandle"}], reg)
    s = _Self()
    s._user_executors["999"] = _FakeExecutor()
    s._operator_ids = {"999"}
    await _run(s)
    assert reg.put_calls == []


@pytest.mark.asyncio
async def test_opt_out_reconcile_removes_only_our_handle(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, [], reg)                   # POSITIVE empty: real opt-out
    s = _Self()
    s._user_board_handles = {"111": "runefox"}
    await _run(s)
    assert reg.removed == ["runefox"]
    assert s._user_board_handles == {}


@pytest.mark.asyncio
async def test_transport_failure_removes_nothing(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, None, reg)                 # None: channel FAILED
    s = _Self()
    s._user_board_handles = {"111": "runefox"}
    await _run(s)
    assert reg.removed == []
    assert s._user_board_handles == {"111": "runefox"}


@pytest.mark.asyncio
async def test_handle_rename_removes_old_row(monkeypatch):
    reg = _FakeRegistry()
    _arm(monkeypatch, [{"telegram_id": "111", "handle": "newfox"}], reg)
    s = _Self()
    s._user_executors["111"] = _FakeExecutor()
    s._user_board_handles = {"111": "oldfox"}
    await _run(s)
    assert reg.removed == ["oldfox"]
    assert [h for h, _ in reg.put_calls] == ["newfox"]


@pytest.mark.asyncio
async def test_default_off_never_fetches(monkeypatch):
    # With the explicit per-user board switch missing, the pull must not run
    # even when the other two gates are open.
    monkeypatch.setenv("PROOFOFPNL_PUBLISH_ENABLED", "1")
    monkeypatch.delenv("PROOFOFPNL_USER_LEADERBOARD_ENABLED", raising=False)
    called = {"n": 0}

    def _count():
        called["n"] += 1
        return []

    monkeypatch.setattr(lp, "fetch_leaderboard_optins", _count)
    await _run(_Self())
    assert called["n"] == 0


def test_run_loop_calls_user_leaderboard_tick():
    src = inspect.getsource(RuneClawEngine.run)
    assert "_maybe_publish_user_leaderboards" in src


def test_source_pins_the_invariants():
    src = inspect.getsource(RuneClawEngine._maybe_publish_user_leaderboards)
    assert "ex is self.live_executor" in src, "isolation guard"
    assert "_is_operator_user" in src
    assert "account_ids=[handle]" in src, "handle-only identity"
    assert "account_ids=[tg" not in src and "account_ids=[str(tg" not in src
    assert "PROOFOFPNL_USER_LEADERBOARD_ENABLED" in src
    assert "proofofpnl_user_scratch" in src, "must not share the operator store"
