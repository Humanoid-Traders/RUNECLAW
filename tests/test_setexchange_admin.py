"""
Admin /setexchange — repair the OPERATOR Bitget credentials into the vault.

Live incident: a wiped .env lost BITGET_PASSPHRASE, so the engine account failed
auth ("bitget requires 'password' credential") and live positions were
unprotected. /setexchange lets an admin re-supply the keys at runtime: validated
read-only, stored ENCRYPTED in the vault (survives future wipes), and the
operator exchange client rebuilt live.

The handler is one huge method file; these guard its wiring by source
inspection, matching the existing telegram-handler test style.
"""

import inspect

from bot.skills.telegram_handler import TelegramHandler


def _src() -> str:
    return inspect.getsource(TelegramHandler._cmd_setexchange)


class TestSetExchangeHandler:
    def test_registered_command(self):
        cls_src = inspect.getsource(TelegramHandler)
        assert '("setexchange", self._cmd_setexchange)' in cls_src

    def test_admin_only(self):
        assert "_is_admin" in _src()

    def test_deletes_secret_message_first(self):
        src = _src()
        # Message deletion must come before the admin gate (keys never linger).
        assert "update.message.delete()" in src
        assert src.index("delete()") < src.index("_is_admin")

    def test_validates_read_only_before_storing(self):
        src = _src()
        assert "validate_bitget_credentials" in src
        # Store only happens after a successful validation branch.
        assert "store_secrets" in src

    def test_persists_all_three_bitget_secrets(self):
        src = _src()
        assert "BITGET_API_KEY" in src
        assert "BITGET_API_SECRET" in src
        assert "BITGET_PASSPHRASE" in src

    def test_rebuilds_operator_exchange_live(self):
        src = _src()
        # Drops the cached operator client + invalidates balance cache so the
        # next call authenticates with the new creds — no restart.
        assert "_exchange = None" in src
        assert "_invalidate_live_balance_cache" in src
