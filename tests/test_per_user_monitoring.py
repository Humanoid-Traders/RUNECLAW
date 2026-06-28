"""
Phase 4: monitoring/reconciliation spans every account (operator + per-user).

Invariants:
  * _all_live_executors() is just [operator] while per-user is OFF, so every
    monitoring loop runs exactly as before; when on, it includes each active
    per-user executor.
  * _rehydrate_user_executors() rebuilds executors for linked users at startup
    (so their persisted positions resume being monitored) — and is a strict
    no-op while per-user is OFF.
  * the credential store can list its user ids for rehydration.

No network, no orders.
"""

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine
from bot.core.live_executor import LiveExecutor


@pytest.fixture
def flag():
    original = engine_mod.CONFIG.per_user_live_enabled

    def _set(value):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", value)

    yield _set
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _store(creds_by_id):
    return type("S", (), {
        "user_ids": staticmethod(lambda: list(creds_by_id.keys())),
        "get": staticmethod(lambda uid: creds_by_id.get(str(uid))),
    })()


def test_all_executors_operator_only_when_off(flag):
    flag(False)
    eng = RuneClawEngine()
    assert eng._all_live_executors() == [eng.live_executor]


def test_rehydrate_is_noop_when_off(flag, monkeypatch):
    flag(False)
    eng = RuneClawEngine()
    # Even if creds exist, OFF means no rehydration and no per-user executors.
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store({"1": {"api_key": "k", "api_secret": "s", "passphrase": "p"}}))
    eng._rehydrate_user_executors()
    assert eng._user_executors == {}
    assert eng._all_live_executors() == [eng.live_executor]


def test_rehydrate_builds_executors_when_on(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    creds = {
        "111": {"api_key": "a", "api_secret": "a", "passphrase": "a"},
        "222": {"api_key": "b", "api_secret": "b", "passphrase": "b"},
    }
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store(creds))
    eng._rehydrate_user_executors()
    assert set(eng._user_executors.keys()) == {"111", "222"}
    everyone = eng._all_live_executors()
    assert eng.live_executor in everyone
    assert len(everyone) == 3
    for ex in eng._user_executors.values():
        assert isinstance(ex, LiveExecutor)
        assert ex is not eng.live_executor


def test_rehydrate_skips_users_without_usable_keys(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    # user 333 is listed but get() returns None (e.g. undecryptable) -> skipped.
    creds = {"111": {"api_key": "a", "api_secret": "a", "passphrase": "a"}, "333": None}
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store(creds))
    eng._rehydrate_user_executors()
    assert set(eng._user_executors.keys()) == {"111"}


def test_store_user_ids(tmp_path):
    from bot.core.exchange_credentials import ExchangeCredentialStore
    from cryptography.fernet import Fernet
    kf = tmp_path / ".k"
    kf.write_bytes(Fernet.generate_key())
    s = ExchangeCredentialStore(creds_file=str(tmp_path / "c.enc"), key_file=str(kf))
    assert s.user_ids() == []
    s.set("111", "apikey_abc123", "apisecret_xyz789", "passphrase1")
    s.set("222", "apikey_def456", "apisecret_uvw012", "passphrase2")
    assert set(s.user_ids()) == {"111", "222"}
