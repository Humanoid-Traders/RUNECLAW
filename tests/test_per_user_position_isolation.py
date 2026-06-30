"""
Per-user position-UI isolation (Telegram view/close layer).

Under PER_USER_LIVE_ENABLED the engine routes each user's live ORDERS to their own
LiveExecutor (engine._executor_for), but the Telegram position view/close layer
was hardcoded to the shared operator executor — so a non-operator user could see
and CLOSE the operator's live positions (and never their own). These tests pin:

  * _caller_executor routes per-user and, crucially, returns None when a
    non-operator caller would fall back to the operator account (no cross-account
    view/close);
  * with the flag OFF every caller resolves to the shared operator executor
    (byte-identical to the prior single-account behaviour);
  * the pos_close owner-tag parser disambiguates the uid from a colon-bearing
    trade_id.
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.skills.telegram_handler import TelegramHandler

OPERATOR = object()   # sentinel: the shared operator executor
USER_EX = object()    # sentinel: a linked user's own executor


class _Users:
    """Not an admin via the user store; live-trade permitted."""
    def get(self, tid):
        return None

    def can_trade_live(self, tid):
        return True


def _engine(linked: dict | None = None):
    linked = linked or {}

    def _executor_for(uid: str = ""):
        # Mirrors engine._executor_for: linked users get their own executor,
        # everyone else falls back to the operator executor.
        return linked.get(str(uid), OPERATOR)

    return SimpleNamespace(live_executor=OPERATOR, _executor_for=_executor_for)


def _handler(engine):
    h = TelegramHandler.__new__(TelegramHandler)
    h.engine = engine
    h.users = _Users()
    return h


def _update(uid):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid)),
        effective_chat=SimpleNamespace(id=int(uid)),
    )


def _cfg(per_user: bool, chat="100", admin="200"):
    p = patch("bot.skills.telegram_handler.CONFIG")
    m = p.start()
    m.per_user_live_enabled = per_user
    m.telegram.chat_id = chat
    m.telegram.admin_ids = admin
    return p


class TestCallerExecutor:
    def test_flag_off_always_operator(self):
        p = _cfg(per_user=False)
        try:
            h = _handler(_engine())
            # Any caller — operator, admin, or stranger — gets the shared executor.
            assert h._caller_executor(_update("100")) is OPERATOR
            assert h._caller_executor(_update("999")) is OPERATOR
        finally:
            p.stop()

    def test_linked_user_gets_own_executor(self):
        p = _cfg(per_user=True)
        try:
            h = _handler(_engine(linked={"300": USER_EX}))
            assert h._caller_executor(_update("300")) is USER_EX
        finally:
            p.stop()

    def test_non_operator_fallback_is_blocked(self):
        # The breach case: a non-linked, non-operator caller would fall back to
        # the operator executor → must be denied (None), not given operator access.
        p = _cfg(per_user=True)
        try:
            h = _handler(_engine(linked={}))
            assert h._caller_executor(_update("300")) is None
        finally:
            p.stop()

    def test_operator_keeps_operator_executor(self):
        p = _cfg(per_user=True)
        try:
            h = _handler(_engine(linked={}))
            # chat_id member owns the operator account.
            assert h._caller_executor(_update("100")) is OPERATOR
        finally:
            p.stop()

    def test_admin_keeps_operator_executor(self):
        p = _cfg(per_user=True)
        try:
            h = _handler(_engine(linked={}))
            # ADMIN_TELEGRAM_IDS member (200) manages the operator account.
            assert h._caller_executor(_update("200")) is OPERATOR
        finally:
            p.stop()


class TestPosCloseOwnerParse:
    def test_simple_uid(self):
        assert TelegramHandler._split_pos_close_owner("TI-abc:222") == ("TI-abc", "222")

    def test_colon_bearing_trade_id(self):
        # Adopted trade_id contains ':' — only the trailing numeric uid is peeled.
        rest = "TI-adopted-BTC-USDT:USDT-1700000000:222"
        assert TelegramHandler._split_pos_close_owner(rest) == (
            "TI-adopted-BTC-USDT:USDT-1700000000", "222")

    def test_untagged_returns_none(self):
        assert TelegramHandler._split_pos_close_owner("TI-abc") == ("TI-abc", None)

    def test_untagged_colon_trade_id_not_misparsed(self):
        # Legacy untagged adopted id (no numeric tail) → owner None, ident intact.
        rest = "TI-adopted-BTC-USDT:USDT-1700000000"
        assert TelegramHandler._split_pos_close_owner(rest) == (rest, None)

    def test_pair_name_returns_none(self):
        assert TelegramHandler._split_pos_close_owner("BTCUSDT") == ("BTCUSDT", None)
