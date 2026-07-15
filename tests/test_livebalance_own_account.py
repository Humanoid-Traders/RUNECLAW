"""
/livebalance must show the CALLER's OWN linked account.

A user who links their Bitget account via /connect (read-only-validated, e.g.
10.16 USDT) then runs /livebalance was shown the shared OPERATOR account
($0.00) instead of their own balance — because the command read
engine.live_executor directly, and the per-user resolver (_executor_for) only
builds a per-user executor when PER_USER_LIVE_ENABLED is on (that flag gates
order PLACEMENT). Viewing your own balance is read-only, so
engine.balance_view_executor() resolves the caller's own account regardless of
the live-trading flag — without enrolling it in the trading/monitoring loops.
"""

from unittest.mock import patch

from bot.core.engine import RuneClawEngine


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng.live_executor = object()          # sentinel shared operator executor
    eng.ws_feed = None
    eng._user_executors = {}
    eng._balance_view_executors = {}
    return eng


class _Store:
    """Minimal stand-in for the encrypted credential store."""

    def __init__(self, creds_by_user):
        self._c = dict(creds_by_user)

    def get(self, uid):
        return self._c.get(str(uid))


def _patch_store(store):
    return patch("bot.core.exchange_credentials.get_credential_store",
                 return_value=store)


_CREDS = {"api_key": "K" * 16, "api_secret": "S" * 16, "passphrase": "pp"}


# ── resolver ────────────────────────────────────────────────────────

class TestBalanceViewExecutor:
    def test_no_linked_account_uses_operator(self):
        eng = _engine()
        with _patch_store(_Store({})):
            assert eng.balance_view_executor("alice") is eng.live_executor

    def test_blank_user_uses_operator(self):
        eng = _engine()
        # No credential lookup should even happen for unattended/auto callers.
        with _patch_store(_Store({"": _CREDS})):
            assert eng.balance_view_executor("") is eng.live_executor
            assert eng.balance_view_executor("auto") is eng.live_executor

    def test_linked_user_gets_own_executor_even_with_live_flag_off(self):
        eng = _engine()
        # balance_view_executor never consults per_user_live_enabled — no CONFIG
        # patch here, and it still resolves the linked account.
        with _patch_store(_Store({"bob": _CREDS})):
            ex = eng.balance_view_executor("bob")
        assert ex is not eng.live_executor
        assert (ex._credentials or {}).get("api_key") == _CREDS["api_key"]

    def test_view_executor_not_enrolled_in_trading_registry(self):
        # Critical: a read-only view account must never land in _user_executors,
        # which all_executors() feeds into the monitoring / reconciliation /
        # close loops. It lives in its own cache.
        eng = _engine()
        with _patch_store(_Store({"bob": _CREDS})):
            eng.balance_view_executor("bob")
        assert "bob" not in eng._user_executors
        assert "bob" in eng._balance_view_executors

    def test_cached_across_calls(self):
        eng = _engine()
        with _patch_store(_Store({"bob": _CREDS})):
            a = eng.balance_view_executor("bob")
            b = eng.balance_view_executor("bob")
        assert a is b

    def test_rebuilt_when_key_changes(self):
        eng = _engine()
        with _patch_store(_Store({"bob": _CREDS})):
            a = eng.balance_view_executor("bob")
        new = {**_CREDS, "api_key": "NEWKEY" + "K" * 10}
        with _patch_store(_Store({"bob": new})):
            b = eng.balance_view_executor("bob")
        assert a is not b
        assert (b._credentials or {}).get("api_key") == new["api_key"]

    def test_invalidate_clears_view_cache(self):
        eng = _engine()
        with _patch_store(_Store({"bob": _CREDS})):
            eng.balance_view_executor("bob")
            assert "bob" in eng._balance_view_executors
            eng.invalidate_user_executor("bob")
            assert "bob" not in eng._balance_view_executors

    def test_lookup_error_falls_back_to_operator(self):
        eng = _engine()

        class _BoomStore:
            def get(self, uid):
                raise RuntimeError("decrypt boom")

        with _patch_store(_BoomStore()):
            assert eng.balance_view_executor("bob") is eng.live_executor
