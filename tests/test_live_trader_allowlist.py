"""
Dedicated live-trader allowlist (LIVE_TRADER_TELEGRAM_IDS).

The live-trade permission allowlist used to be built only from TELEGRAM_CHAT_ID +
ADMIN_TELEGRAM_IDS — the same vars that flag a user as an operator/admin. So the
only way to let a regular user trade live ALSO made them an operator, which
bypassed per-user risk isolation (#122) and per-user equity sizing (#124) and
granted admin commands.

LIVE_TRADER_TELEGRAM_IDS grants live-trade permission WITHOUT operator/admin
identity: members ARE on the bot + live-trade allowlist, but are NOT operators
(so per-user routing engages) and NOT admins (no admin commands).
"""

from types import SimpleNamespace
from unittest.mock import patch

from bot.core.engine import RuneClawEngine
from bot.skills.telegram_handler import TelegramHandler


# ── _is_operator_user (engine) — the security crux ──────────────────

def _eng_cfg(chat="100", admin="200", live="300"):
    p = patch("bot.core.engine.CONFIG")
    m = p.start()
    m.telegram.chat_id = chat
    m.telegram.admin_ids = admin
    m.telegram.live_trader_ids = live  # present for realism; NOT read by _is_operator_user
    return p


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._user_store = None
    return eng


class TestIsOperatorUser:
    def test_chat_id_member_is_operator(self):
        p = _eng_cfg()
        try:
            assert _engine()._is_operator_user("100") is True
        finally:
            p.stop()

    def test_admin_member_is_operator(self):
        p = _eng_cfg()
        try:
            assert _engine()._is_operator_user("200") is True
        finally:
            p.stop()

    def test_live_trader_is_NOT_operator(self):
        # THE fix: a live trader is permitted to trade, but is not an operator,
        # so risk_for() / per_user_live_eligibility / _live_recheck_context all
        # treat them as a regular per-user account.
        p = _eng_cfg()
        try:
            assert _engine()._is_operator_user("300") is False
        finally:
            p.stop()

    def test_unknown_is_not_operator(self):
        p = _eng_cfg()
        try:
            assert _engine()._is_operator_user("999") is False
        finally:
            p.stop()


# ── allowlist + admin gating (telegram handler) ─────────────────────

def _tele_cfg(chat="100", admin="200", live="300"):
    p = patch("bot.skills.telegram_handler.CONFIG")
    m = p.start()
    m.telegram.chat_id = chat
    m.telegram.admin_ids = admin
    m.telegram.live_trader_ids = live
    return p


class _Users:
    def __init__(self, live_ok=True):
        self._live_ok = live_ok

    def get(self, tid):
        return None  # not an admin via user store

    def can_trade_live(self, tid):
        return self._live_ok


def _handler(users=None):
    h = TelegramHandler.__new__(TelegramHandler)
    h.users = users or _Users()
    return h


def _update(uid):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=int(uid)),
        effective_chat=SimpleNamespace(id=int(uid)),
    )


class TestAllowlist:
    def test_allowlist_includes_all_three_sources(self):
        p = _tele_cfg()
        try:
            assert _handler()._allowlist_ids() == {"100", "200", "300"}
        finally:
            p.stop()

    def test_live_trader_is_allowlisted(self):
        p = _tele_cfg()
        try:
            assert _handler()._is_allowlisted(_update("300")) is True
        finally:
            p.stop()

    def test_live_trader_can_trade_live(self):
        p = _tele_cfg()
        try:
            assert _handler()._can_trade_live("300") is True
        finally:
            p.stop()

    def test_non_allowlisted_cannot_trade_live(self):
        p = _tele_cfg()
        try:
            # Even if the user store flag says yes, the allowlist blocks it.
            assert _handler(_Users(live_ok=True))._can_trade_live("999") is False
        finally:
            p.stop()

    def test_live_trader_needs_user_store_flag_too(self):
        p = _tele_cfg()
        try:
            # On the allowlist but no /grant_live → still cannot trade live.
            assert _handler(_Users(live_ok=False))._can_trade_live("300") is False
        finally:
            p.stop()


class TestAdminGating:
    def test_live_trader_is_not_admin(self):
        p = _tele_cfg()
        try:
            # 300 is on the live allowlist but NOT in ADMIN_TELEGRAM_IDS and not
            # an admin in the user store → no admin powers.
            assert _handler()._is_admin(_update("300")) is False
        finally:
            p.stop()

    def test_admin_id_is_admin(self):
        p = _tele_cfg()
        try:
            assert _handler()._is_admin(_update("200")) is True
        finally:
            p.stop()
