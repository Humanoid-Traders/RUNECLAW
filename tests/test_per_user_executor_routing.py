"""
Phase 3: engine routes a confirmed live trade to the right LiveExecutor.

The safety-critical invariant: with PER_USER_LIVE_ENABLED OFF (the default),
_executor_for() ALWAYS returns the shared operator executor — no per-user
executor is ever created, so the live path is byte-identical to before. Only
when the flag is on does a human user's confirmed trade resolve to their OWN
linked-account executor; 'auto'/'' (admin auto-trade, unattended) still use the
operator account.

No network and no orders: we only exercise the resolver + cache, never execute().
CONFIG is a frozen dataclass, so the flag is flipped via object.__setattr__ and
restored after each test by the `flag` fixture.
"""

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor


@pytest.fixture
def flag():
    """Set CONFIG.per_user_live_enabled for the duration of a test, then restore."""
    original = engine_mod.CONFIG.per_user_live_enabled

    def _set(value):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", value)

    yield _set
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _store_returning(creds_or_fn):
    get = creds_or_fn if callable(creds_or_fn) else (lambda uid: creds_or_fn)
    return type("S", (), {"get": staticmethod(get)})()


def test_flag_off_always_operator(flag):
    flag(False)
    eng = RuneClawEngine()
    op = eng.live_executor
    assert eng._executor_for("12345") is op
    assert eng._executor_for("auto") is op
    assert eng._executor_for("") is op
    assert eng._user_executors == {}  # nothing ever created while OFF


def test_flag_on_auto_and_unattended_use_operator(flag):
    flag(True)
    eng = RuneClawEngine()
    op = eng.live_executor
    assert eng._executor_for("auto") is op
    assert eng._executor_for("") is op


def test_flag_on_user_without_creds_falls_back_to_operator(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store_returning(None))
    assert eng._executor_for("777") is eng.live_executor


def test_flag_on_user_with_creds_gets_own_executor(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    creds = {"api_key": "uk", "api_secret": "us", "passphrase": "up"}
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store_returning(creds))
    ex = eng._executor_for("555")
    assert isinstance(ex, LiveExecutor)
    assert ex is not eng.live_executor          # NOT the operator executor
    assert ex.user_id == "555"
    assert ex._credentials == creds
    # Cached: same object on the next call.
    assert eng._executor_for("555") is ex
    assert eng._user_executors["555"] is ex


def test_rebuild_on_key_change(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    box = {"creds": {"api_key": "k1", "api_secret": "s", "passphrase": "p"}}
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store_returning(lambda uid: box["creds"]))
    first = eng._executor_for("9")
    # User re-/connects with a new api_key -> resolver must rebuild.
    box["creds"] = {"api_key": "k2", "api_secret": "s", "passphrase": "p"}
    second = eng._executor_for("9")
    assert second is not first
    assert second._credentials["api_key"] == "k2"


def test_invalidate_user_executor(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    creds = {"api_key": "uk", "api_secret": "us", "passphrase": "up"}
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store_returning(creds))
    eng._executor_for("3")
    assert "3" in eng._user_executors
    eng.invalidate_user_executor("3")
    assert "3" not in eng._user_executors
    # Idempotent — safe when nothing is cached.
    eng.invalidate_user_executor("3")
