"""Operator web-live readiness + enablement — predictions R1–R4.

R1 readiness sources every input; R2 flipping the opt-in changes the checklist;
R3 set_user_enabled is web-only; R4 the human card lists all five checks.
"""
import tempfile
import os

import pytest

from bot.web import web_live_admin as adm
from bot.web import web_live_gate
from bot.guardian import user_authority_store as uas
from bot.guardian.authority import compile_envelope


class _Users:
    def __init__(self):
        self._flags = {}
    def web_live_enabled(self, tg):
        return bool(self._flags.get(str(tg)))
    def set_web_live_enabled(self, tg, val):
        if not str(tg).startswith("web:"):
            return False
        self._flags[str(tg)] = bool(val)
        return True


@pytest.fixture
def wired(monkeypatch, tmp_path):
    import types
    # feature switch ON, bot live ON
    monkeypatch.setenv("WEB_LIVE_TRADING_ENABLED", "1")
    monkeypatch.setattr(adm, "CONFIG", types.SimpleNamespace(is_live=lambda: True))
    # own keys present
    monkeypatch.setattr("bot.core.exchange_credentials.get_credential_store",
                        lambda: types.SimpleNamespace(has=lambda tg: True))
    # enforce-mode envelope bound
    store = uas.UserAuthorityStore(str(tmp_path / "ua.json"))
    env = compile_envelope({"mode": "enforce", "allowed_venues": ["bitget"],
                            "symbol_allowlist": ["BTC"], "max_notional_per_trade_usd": 100})
    store.bind("web:5", env)
    monkeypatch.setattr(uas, "_STORE", store)
    return _Users()


def test_r1_all_met_is_ready(wired):
    users = wired
    users.set_web_live_enabled("web:5", True)
    r = adm.user_readiness(users, "web:5")
    assert r["allowed"] is True
    assert all(r["checklist"].values())


def test_r2_missing_opt_in_blocks(wired):
    users = wired                                   # opt-in still False
    r = adm.user_readiness(users, "web:5")
    assert r["allowed"] is False
    assert r["checklist"]["user_opted_in"] is False
    assert r["checklist"]["envelope_enforcing"] is True   # the others are met
    # flipping it on flips readiness
    assert adm.set_user_enabled(users, "web:5", True) is True
    assert adm.user_readiness(users, "web:5")["allowed"] is True


def test_r3_set_enabled_is_web_only(wired):
    users = wired
    assert adm.set_user_enabled(users, "12345", True) is False   # telegram id rejected
    assert adm.set_user_enabled(users, "web:5", True) is True


def test_r4_human_card_lists_all_checks(wired):
    users = wired
    card = adm.human_readable("web:5", adm.user_readiness(users, "web:5"))
    for label in adm._CHECK_LABELS.values():
        assert label in card
    assert "web:5" in card


def test_r4_feature_off_default(monkeypatch):
    monkeypatch.delenv("WEB_LIVE_TRADING_ENABLED", raising=False)
    assert web_live_gate.feature_enabled(env={}) is False
