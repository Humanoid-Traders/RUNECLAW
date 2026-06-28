"""
Phase 5: per-user live-trading eligibility gate.

The safety rule, enforced in confirm_trade via engine.per_user_live_eligibility:
when PER_USER_LIVE_ENABLED is ON, a regular (non-operator) human user may only
place a live order if they have linked their OWN decryptable keys — otherwise
the trade is REJECTED rather than silently routed to the operator account.
Operator/admin users and the auto/unattended paths always pass. While the flag
is OFF the gate is a strict no-op (every caller is eligible), so the operator
path is unchanged.
"""

import pytest

import bot.core.engine as engine_mod
from bot.core.engine import RuneClawEngine


@pytest.fixture
def flag():
    original = engine_mod.CONFIG.per_user_live_enabled

    def _set(value):
        object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", value)

    yield _set
    object.__setattr__(engine_mod.CONFIG, "per_user_live_enabled", original)


def _store(creds_by_id):
    return type("S", (), {"get": staticmethod(lambda uid: creds_by_id.get(str(uid)))})()


def test_flag_off_everyone_eligible(flag):
    flag(False)
    eng = RuneClawEngine()
    for uid in ("auto", "", "12345", "999"):
        ok, _ = eng.per_user_live_eligibility(uid)
        assert ok is True


def test_auto_and_unattended_always_eligible(flag):
    flag(True)
    eng = RuneClawEngine()
    assert eng.per_user_live_eligibility("auto")[0] is True
    assert eng.per_user_live_eligibility("")[0] is True


def test_regular_user_without_keys_rejected(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    # Not an operator/admin, no linked keys -> rejected.
    monkeypatch.setattr(eng, "_is_operator_user", lambda uid: False)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store({}))
    ok, reason = eng.per_user_live_eligibility("777")
    assert ok is False
    assert "connect" in reason.lower()


def test_regular_user_with_keys_eligible(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    monkeypatch.setattr(eng, "_is_operator_user", lambda uid: False)
    monkeypatch.setattr(
        "bot.core.exchange_credentials.get_credential_store",
        lambda: _store({"777": {"api_key": "k", "api_secret": "s", "passphrase": "p"}}))
    ok, _ = eng.per_user_live_eligibility("777")
    assert ok is True


def test_operator_admin_eligible_without_own_keys(flag, monkeypatch):
    flag(True)
    eng = RuneClawEngine()
    # Admin/operator users trade the operator account; no own keys required.
    monkeypatch.setattr(eng, "_is_operator_user", lambda uid: True)
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: _store({}))
    ok, _ = eng.per_user_live_eligibility("111")
    assert ok is True


def test_is_operator_user_via_admin_allowlist(flag):
    flag(True)
    eng = RuneClawEngine()
    import bot.core.engine as em
    # Patch the admin env allowlist to include 222.
    orig_admin = em.CONFIG.telegram.admin_ids
    try:
        object.__setattr__(em.CONFIG.telegram, "admin_ids", "222")
        assert eng._is_operator_user("222") is True
        assert eng._is_operator_user("999") is False
    finally:
        object.__setattr__(em.CONFIG.telegram, "admin_ids", orig_admin)
