"""
Live-equity truthfulness: /start, /status, /portfolio and the AI chat context
must never show the paper $10,000 baseline while the bot is in LIVE mode.

Operator report: Telegram /start showed "Equity $10,000.00" with mode LIVE and
a real open position. Root cause: the display resolvers fell back to
portfolio.snapshot().equity_usd (paper $10k) whenever the live balance fetch
returned falsy — so a transient auth/network failure masqueraded as a $10k
paper account on a live, funded account.

Fix: engine.resolve_display_equity (async) and resolve_display_equity_sync
return (None, "unavailable") in LIVE mode when the balance can't be read — the
callers render "unavailable", never the paper baseline. A genuinely empty live
account still shows a truthful $0.00 (the balance dict is present, total is 0).
"""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.core.engine import RuneClawEngine


def _engine():
    eng = RuneClawEngine.__new__(RuneClawEngine)
    eng._live_balance_cache = {}
    _paper_pf = MagicMock()
    _paper_pf.snapshot.return_value = MagicMock(equity_usd=10_000.0)
    eng.user_portfolios = {"op": _paper_pf}
    eng.portfolio = _paper_pf
    return eng


def _live():
    cfg = MagicMock()
    cfg.is_live.return_value = True
    return cfg


def _paper():
    cfg = MagicMock()
    cfg.is_live.return_value = False
    return cfg


class TestResolveDisplayEquityAsync:
    async def test_live_available_returns_real_equity(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value={"total": 17.30})
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "live"
        assert val == 17.30

    async def test_live_unavailable_is_none_not_paper(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value=None)
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "unavailable"
        assert val is None, "must never substitute the paper $10k baseline"

    async def test_empty_live_account_shows_truthful_zero(self):
        eng = _engine()
        eng.get_user_live_equity = AsyncMock(return_value={"total": 0.0})
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "live"
        assert val == 0.0, "a real empty account is $0.00, not $10k, not unavailable"

    async def test_paper_mode_returns_paper_equity(self):
        eng = _engine()
        with patch("bot.core.engine.CONFIG", _paper()):
            val, src = await eng.resolve_display_equity("op")
        assert src == "paper"
        assert val == 10_000.0


class TestResolveDisplayEquitySync:
    def test_live_cache_hit_returns_live(self):
        eng = _engine()
        eng._live_balance_cache = {"total": 42.5}
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "live"
        assert val == 42.5

    def test_live_empty_cache_is_unavailable_not_paper(self):
        eng = _engine()
        eng._live_balance_cache = {}
        with patch("bot.core.engine.CONFIG", _live()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "unavailable"
        assert val is None

    def test_paper_mode_returns_paper_equity(self):
        eng = _engine()
        with patch("bot.core.engine.CONFIG", _paper()):
            val, src = eng.resolve_display_equity_sync("op")
        assert src == "paper"
        assert val == 10_000.0
