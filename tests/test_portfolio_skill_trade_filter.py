"""
GetPortfolioSkill's live-mode trade count/win-rate must match /performance.

Regression for a real bug: this skill excluded orphan-adopted trades but not
never-filled orders (canceled/expired/price_drift/rejected close at $0 PnL),
while /performance and /portfolio excluded both. The result was two surfaces
disagreeing on trade count and win rate for the same account (e.g. 198 trades
at 38% win rate here vs 145 trades at 52% win rate on /performance) even
though both agreed on total realized PnL — the extra zero-PnL non-fills
inflated the denominator without ever counting as a win.
"""

import types

import pytest

from bot.skills.skill_registry import GetPortfolioSkill


def _pos(symbol, pnl, close_reason="", trade_id="T1", cost_usd=10.0):
    return types.SimpleNamespace(
        symbol=symbol, direction="LONG", pnl_usd=pnl, commission=0.0,
        close_reason=close_reason, trade_id=trade_id, cost_usd=cost_usd,
        leverage=1,
    )


class _FakeExecutor:
    def __init__(self, closed):
        self.open_positions = []
        self.closed_positions = closed
        self.total_exposure_usd = 0.0


class _FakeEngine:
    def __init__(self, closed):
        self.live_executor = _FakeExecutor(closed)

    async def get_effective_equity_async(self, user_id):
        return 100.0


@pytest.mark.asyncio
async def test_never_filled_orders_excluded_from_trade_count_and_win_rate(monkeypatch):
    import bot.config as config_mod
    fake_config = types.SimpleNamespace(is_live=lambda: True)
    monkeypatch.setattr(config_mod, "CONFIG", fake_config)

    closed = [
        _pos("BTC/USDT", 10.0, trade_id="A1"),       # real win
        _pos("ETH/USDT", -5.0, trade_id="A2"),       # real loss
        _pos("SOL/USDT", 0.0, close_reason="canceled", trade_id="A3"),
        _pos("DOT/USDT", 0.0, close_reason="expired", trade_id="A4"),
        _pos("XRP/USDT", 0.0, close_reason="price_drift", trade_id="A5"),
        _pos("ADA/USDT", 0.0, close_reason="rejected", trade_id="A6"),
        _pos("ORPHAN/USDT", 999.0, trade_id="TI-adopted-1"),  # excluded orphan
    ]
    engine = _FakeEngine(closed)

    result = await GetPortfolioSkill().execute(engine, user_id="u1")

    # Only the 2 genuine trades should count: 1 win / 2 total = 50%.
    assert "Trades: <code>2</code>" in result
    assert "Win rate: <code>50%</code>" in result
    # Realized PnL only reflects the genuine trades (10 - 5 = 5), never the
    # $999 orphan or the $0 non-fills.
    assert "$+5.00" in result
