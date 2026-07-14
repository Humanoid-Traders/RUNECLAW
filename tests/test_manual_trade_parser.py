"""Shared manual-trade parser (bot/skills/manual_trade.py).

Extracted from TelegramHandler._parse_manual_trade so Telegram and the web
user gateway validate trades identically. These cases pin the original
grammar and error behavior, plus the idea-construction/registration helpers.
"""

from types import SimpleNamespace

from bot.skills.manual_trade import (parse_manual_trade, build_manual_idea,
                                     register_manual_idea)


class TestParse:
    def test_basic_buy(self):
        assert parse_manual_trade("buy SOL 71.42 sl 70.05 tp 76.42") == \
            ("LONG", "SOL", 71.42, 70.05, 76.42, None)

    def test_short_with_margin(self):
        assert parse_manual_trade("short ETH 1721 sl 1795 tp 1642 margin 250") == \
            ("SHORT", "ETH", 1721.0, 1795.0, 1642.0, 250.0)

    def test_sell_is_short_and_long_is_long(self):
        assert parse_manual_trade("sell BTC 100 sl 105 tp 90")[0] == "SHORT"
        assert parse_manual_trade("long BTC 100 sl 95 tp 110")[0] == "LONG"

    def test_dollar_signs_and_commas(self):
        d, s, entry, sl, tp, m = parse_manual_trade(
            "buy BTC $61,250.50 sl $60,000 tp $65,000 margin $1,000")
        assert (entry, sl, tp, m) == (61250.50, 60000.0, 65000.0, 1000.0)

    def test_invalid_format_returns_help(self):
        err = parse_manual_trade("just some words")
        assert isinstance(err, str) and "Invalid format" in err

    def test_long_sl_above_entry_rejected(self):
        err = parse_manual_trade("buy SOL 71 sl 72 tp 76")
        assert isinstance(err, str) and "must be below entry" in err

    def test_long_tp_below_entry_rejected(self):
        err = parse_manual_trade("buy SOL 71 sl 70 tp 69")
        assert isinstance(err, str) and "must be above entry" in err

    def test_short_sl_below_entry_rejected(self):
        err = parse_manual_trade("short SOL 71 sl 70 tp 65")
        assert isinstance(err, str) and "must be above entry" in err

    def test_short_tp_above_entry_rejected(self):
        err = parse_manual_trade("short SOL 71 sl 72 tp 75")
        assert isinstance(err, str) and "must be below entry" in err


class TestBuildAndRegister:
    def test_build_manual_idea_shape(self):
        idea = build_manual_idea("LONG", "SOL", 71.0, 70.0, 76.0)
        assert idea.asset == "SOL/USDT:USDT"
        assert idea.source == "manual"
        assert idea.order_type == "limit"
        assert idea.confidence == 1.0
        assert idea.risk_reward_ratio == 5.0

    def test_register_sets_pending_and_margin(self):
        engine = SimpleNamespace(_pending_ideas={})
        idea = build_manual_idea("SHORT", "ETH", 1721.0, 1795.0, 1642.0)
        register_manual_idea(engine, idea, 250.0)
        assert engine._pending_ideas[idea.id] is idea
        assert engine._manual_margin_override[idea.id] == 250.0

    def test_register_without_margin_leaves_no_override(self):
        engine = SimpleNamespace(_pending_ideas={})
        idea = build_manual_idea("LONG", "SOL", 71.0, 70.0, 76.0)
        register_manual_idea(engine, idea, None)
        assert not getattr(engine, "_manual_margin_override", {})
