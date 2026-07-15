"""
Staged per-user live rollout — the ALLOWLIST gate at execution time.

The user chose a staged allowlist rollout: even with PER_USER_LIVE_ENABLED on and
their own keys linked, a regular user's live trade must ALSO clear the live
allowlist (admin /grant_live → user_store.set_live_trading). Previously
per_user_live_eligibility required only linked keys, so flipping the master
switch would open live to every key-holder at once. This pins the gate:

  - operator/admin            → allowed
  - no linked keys            → rejected
  - keys but NOT allowlisted  → rejected  (the staging gate)
  - keys AND allowlisted      → allowed
  - user store unavailable    → rejected  (fail-closed for real money)

Plus a guard that the manual /trade override re-applies the per-user cap and the
free-margin clamp (C5) instead of bypassing them.
"""

import inspect
from unittest.mock import MagicMock, patch

from bot.core.engine import RuneClawEngine


def _engine(store, human=True, operator=False):
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._user_store = store
    eng._human_confirmed = MagicMock(return_value=human)
    eng._is_operator_user = MagicMock(return_value=operator)
    return eng


def _cfg_live_per_user():
    cfg = MagicMock()
    cfg.per_user_live_enabled = True
    return cfg


def _patch_store(creds_present):
    store = MagicMock()
    store.get.return_value = {"api_key": "k"} if creds_present else None
    return patch("bot.core.exchange_credentials.get_credential_store",
                 return_value=store)


class TestAllowlistEligibility:
    def test_operator_always_allowed(self):
        eng = _engine(store=MagicMock(), operator=True)
        with patch("bot.core.engine.CONFIG", _cfg_live_per_user()):
            ok, _ = eng.per_user_live_eligibility("op")
        assert ok is True

    def test_no_keys_rejected(self):
        store = MagicMock()
        eng = _engine(store=store)
        with patch("bot.core.engine.CONFIG", _cfg_live_per_user()), _patch_store(False):
            ok, reason = eng.per_user_live_eligibility("alice")
        assert ok is False and "link" in reason.lower()

    def test_keys_but_not_allowlisted_rejected(self):
        store = MagicMock(); store.can_trade_live.return_value = False
        eng = _engine(store=store)
        with patch("bot.core.engine.CONFIG", _cfg_live_per_user()), _patch_store(True):
            ok, reason = eng.per_user_live_eligibility("alice")
        assert ok is False and "allowlist" in reason.lower()

    def test_keys_and_allowlisted_allowed(self):
        store = MagicMock(); store.can_trade_live.return_value = True
        eng = _engine(store=store)
        with patch("bot.core.engine.CONFIG", _cfg_live_per_user()), _patch_store(True):
            ok, reason = eng.per_user_live_eligibility("alice")
        assert ok is True and "allowlist" in reason.lower()

    def test_store_unavailable_fails_closed(self):
        eng = _engine(store=None)
        with patch("bot.core.engine.CONFIG", _cfg_live_per_user()), _patch_store(True):
            ok, reason = eng.per_user_live_eligibility("alice")
        assert ok is False and "fail-closed" in reason.lower()

    def test_per_user_off_is_operator_account(self):
        cfg = MagicMock(); cfg.per_user_live_enabled = False
        eng = _engine(store=MagicMock())
        with patch("bot.core.engine.CONFIG", cfg):
            ok, _ = eng.per_user_live_eligibility("alice")
        assert ok is True  # per-user off → everything runs on the operator account


class TestManualOverrideRespectsCapAndClamp:
    def _block(self) -> str:
        src = inspect.getsource(RuneClawEngine._confirm_trade_inner)
        i = src.index("Manual margin override: if user specified")
        j = src.index("C2-53 FIX", i)
        return src[i:j]

    def test_manual_override_reapplies_per_user_cap(self):
        b = self._block()
        assert "_per_user_margin_cap(user_id)" in b
        assert "manual_margin_cap" in b

    def test_manual_override_reapplies_free_balance_clamp(self):
        b = self._block()
        assert "manual_margin_clamp" in b
        assert 'live_bal.get("free"' in b
