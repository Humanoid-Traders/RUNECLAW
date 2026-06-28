"""
_can_trade_live is THE single authority for 'may this Telegram user place LIVE
orders'. It is defense-in-depth: BOTH the operator env allowlist AND the per-user
UserStore flag must permit it. Centralizing the 7 former scattered reads here means
every gate and status display agree, and a stale users.json flag can never let a
non-allowlisted user trade live.
"""

import dataclasses

import bot.config as cfg_mod
from bot.config import CONFIG
from bot.core.engine import RuneClawEngine
from bot.skills.telegram_handler import TelegramHandler


def _handler():
    return TelegramHandler(RuneClawEngine())


def _with_allowlist(monkeypatch, chat_id="", admin_ids=""):
    new_tg = dataclasses.replace(CONFIG.telegram, chat_id=chat_id, admin_ids=admin_ids)
    new_cfg = dataclasses.replace(CONFIG, telegram=new_tg)
    monkeypatch.setattr(cfg_mod, "CONFIG", new_cfg)
    # telegram_handler reads CONFIG at call time via the module global.
    monkeypatch.setattr("bot.skills.telegram_handler.CONFIG", new_cfg, raising=False)
    return new_cfg


class TestCanTradeLiveAuthority:
    def test_allowlisted_user_with_flag_can_trade(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111")
        h.users.register(111, name="op")
        h.users.set_live_trading(111, True)
        assert h._can_trade_live(111) is True

    def test_non_allowlisted_user_blocked_even_with_stale_flag(self, monkeypatch):
        # The divergence edge: users.json says yes, but the operator allowlist
        # does not include them → the authority must say NO.
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111")
        h.users.register(222, name="stranger")
        h.users.set_live_trading(222, True)   # stale/leftover flag
        assert h._can_trade_live(222) is False

    def test_allowlisted_but_no_flag_cannot_trade(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="111,222")
        h.users.register(222, name="viewer")  # no live flag granted
        assert h._can_trade_live(222) is False

    def test_empty_allowlist_falls_back_to_flag(self, monkeypatch):
        # Demo/paper (no allowlist configured) → identical to the prior behaviour:
        # the UserStore flag alone decides.
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="", admin_ids="")
        h.users.register(333, name="demo")
        assert h._can_trade_live(333) is False
        h.users.set_live_trading(333, True)
        assert h._can_trade_live(333) is True

    def test_accepts_int_or_str_id(self, monkeypatch):
        h = _handler()
        _with_allowlist(monkeypatch, chat_id="444")
        h.users.register(444, name="op")
        h.users.set_live_trading(444, True)
        assert h._can_trade_live(444) is True
        assert h._can_trade_live("444") is True
