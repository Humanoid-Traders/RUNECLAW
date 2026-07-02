"""Tier 1: order-flow gate resurrection (Gate 2 + Rule 20).

Audit findings: set_order_flow_analyzer had zero callers so both gates always
took their fail-open skip branch; Rule 20 was LONG-only (shorts had a
structural free pass) and compared the spoofable full-book sums; the taker
3-bar gate failed CLOSED on <3 bars, which at one-bar-per-scan cadence made
every fresh symbol untradeable; and the cached signal fed to Rule 20 was
never checked for symbol or freshness, so a LONG on symbol B could be gated
by symbol A's book.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest

from bot.compat import UTC
from bot.core.order_flow import OrderFlowAnalyzer, OrderFlowConfig, OrderFlowSignal
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.utils.models import Direction, TradeIdea


def _sig(symbol="BTC/USDT", bid_top=0.0, ask_top=0.0, bid=100_000.0,
         ask=100_000.0, age_s=0, book_ok=True):
    s = OrderFlowSignal(symbol=symbol)
    s.bid_depth_usd = bid
    s.ask_depth_usd = ask
    s.bid_depth_top_usd = bid_top
    s.ask_depth_top_usd = ask_top
    if book_ok:
        s.components_ok = ["book"]
    s.timestamp = datetime.now(UTC) - timedelta(seconds=age_s)
    return s


class TestRule20Symmetric:
    def _an(self):
        return OrderFlowAnalyzer(OrderFlowConfig())

    def test_short_no_longer_free_pass(self):
        # Bid-heavy book must now REJECT a short (old code returned
        # "only applies to LONG entries" and passed everything).
        an = self._an()
        res = an.check_bid_dominance(_sig(bid_top=300_000, ask_top=100_000), "SHORT")
        assert res["passed"] is False
        assert "ask" in res["reason"]

    def test_short_confirmed_by_ask_dominance(self):
        an = self._an()
        res = an.check_bid_dominance(_sig(bid_top=100_000, ask_top=200_000), "SHORT")
        assert res["passed"] is True

    def test_long_uses_top_depth_not_full_sums(self):
        # Full sums say 3:1 bids but the executable top-5 levels say 1:1 —
        # the spoof-resistant top depth must win.
        an = self._an()
        res = an.check_bid_dominance(
            _sig(bid=300_000, ask=100_000, bid_top=50_000, ask_top=50_000), "LONG")
        assert res["passed"] is False

    def test_missing_book_fail_closed(self):
        an = self._an()
        res = an.check_bid_dominance(_sig(book_ok=False), "LONG")
        assert res["passed"] is False

    def test_ratio_configurable(self):
        an = OrderFlowAnalyzer(OrderFlowConfig(dominance_required_ratio=2.0))
        res = an.check_bid_dominance(_sig(bid_top=150_000, ask_top=100_000), "LONG")
        assert res["passed"] is False  # 1.5 < 2.0
        an2 = OrderFlowAnalyzer(OrderFlowConfig(dominance_required_ratio=1.2))
        res2 = an2.check_bid_dominance(_sig(bid_top=150_000, ask_top=100_000), "LONG")
        assert res2["passed"] is True


class TestRiskGateSignalGuards:
    def _engine(self):
        state = os.path.join(tempfile.mkdtemp(prefix="rc-ofgate-"), "risk_state.json")
        eng = RiskEngine(PortfolioTracker(initial_balance=10_000.0), state_file=state)
        eng.set_order_flow_analyzer(OrderFlowAnalyzer(OrderFlowConfig()))
        return eng

    def _idea(self, asset="BTC/USDT"):
        return TradeIdea(
            asset=asset, direction=Direction.LONG, entry_price=100.0,
            stop_loss=95.0, take_profit=110.0, confidence=0.9,
            reasoning="gate test", source="scan", timestamp=datetime.now(UTC))

    def test_other_symbols_signal_is_ignored(self):
        # An ask-heavy ETH book must NOT gate a BTC long — the mismatched
        # signal is skipped fail-open.
        eng = self._engine()
        eng.set_order_flow_signal(_sig(symbol="ETH/USDT",
                                       bid_top=50_000, ask_top=500_000))
        chk = eng.evaluate(self._idea("BTC/USDT"))
        assert not [f for f in chk.checks_failed if f.startswith("BID_DOMINANCE")]
        assert any("skipped" in p for p in chk.checks_passed if p.startswith("BID_DOMINANCE"))

    def test_stale_signal_is_ignored(self):
        eng = self._engine()
        eng.set_order_flow_signal(_sig(symbol="BTC/USDT", bid_top=50_000,
                                       ask_top=500_000, age_s=600))
        chk = eng.evaluate(self._idea("BTC/USDT"))
        assert not [f for f in chk.checks_failed if f.startswith("BID_DOMINANCE")]

    def test_fresh_same_symbol_signal_is_applied(self):
        # Ask-heavy fresh BTC book must fail a BTC long via Rule 20.
        eng = self._engine()
        eng.set_order_flow_signal(_sig(symbol="BTC/USDT", bid_top=50_000,
                                       ask_top=500_000, age_s=5))
        chk = eng.evaluate(self._idea("BTC/USDT"))
        assert [f for f in chk.checks_failed if f.startswith("BID_DOMINANCE")]


class TestExchangeFlowAsyncFactory:
    @pytest.mark.asyncio
    async def test_async_factory_is_awaited(self):
        # The engine passes MarketScanner._get_exchange — an async factory.
        # The old sync call returned a coroutine object and every fetch
        # raised into the broad except; funding was permanently None.
        from bot.core.exchange_flow import ExchangeFlowProvider

        class FakeExchange:
            async def fetch_funding_rate(self, symbol):
                return {"fundingRate": 0.0004, "symbol": symbol}

        async def factory():
            return FakeExchange()

        p = ExchangeFlowProvider(exchange_factory=factory)
        rate = await p.get_funding_rate("BTC/USDT:USDT")
        assert rate == pytest.approx(0.0004)

    @pytest.mark.asyncio
    async def test_sync_factory_still_works(self):
        from bot.core.exchange_flow import ExchangeFlowProvider

        class FakeExchange:
            async def fetch_funding_rate(self, symbol):
                return {"fundingRate": -0.0002}

        p = ExchangeFlowProvider(exchange_factory=lambda: FakeExchange())
        rate = await p.get_funding_rate("BTC/USDT:USDT")
        assert rate == pytest.approx(-0.0002)
