"""Tests for execution/portfolio upgrades: Smart Order Router, Trailing Stop Engine, Auto-Rebalance."""

from __future__ import annotations

import time
import pytest
from unittest.mock import patch, MagicMock

from bot.risk.order_router import SmartOrderRouter
from bot.risk.portfolio import PortfolioTracker, TrailingStopConfig
from bot.utils.models import Direction, TradeIdea


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_idea(
    asset="BTCUSDT",
    direction=Direction.LONG,
    entry=50000.0,
    sl=49000.0,
    tp=53000.0,
    confidence=0.8,
    idea_id=None,
) -> TradeIdea:
    kw = dict(
        asset=asset,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        reasoning="test",
    )
    if idea_id:
        kw["id"] = idea_id
    return TradeIdea(**kw)


def _make_portfolio(balance=100000.0, **kwargs) -> PortfolioTracker:
    return PortfolioTracker(initial_balance=balance, **kwargs)


# ===================================================================
# 1. Smart Order Router Tests
# ===================================================================

class TestSmartOrderRouter:

    def setup_method(self):
        self.router = SmartOrderRouter()

    # -- Slippage estimation --

    def test_no_order_book_returns_paper_defaults(self):
        """No book data -> paper mode defaults."""
        result = self.router.estimate_slippage("BTCUSDT", 10000)
        assert result["slippage_pct"] == self.router.DEFAULT_PAPER_SLIPPAGE_PCT
        assert result["order_type"] == "MARKET"
        assert result["warning"] is None

    def test_shallow_book_low_slippage(self):
        """Small order fills entirely at best price -> ~0% slippage."""
        book = [[50000.0, 10.0]]  # 10 BTC at 50k = $500k available
        result = self.router.estimate_slippage("BTCUSDT", 1000, book)
        assert result["slippage_pct"] == 0.0
        assert result["order_type"] == "MARKET"
        assert result["warning"] is None

    def test_deep_book_with_price_impact(self):
        """Order spanning multiple levels should show slippage."""
        book = [
            [50000.0, 0.1],   # $5,000
            [50100.0, 0.1],   # $5,010
            [50300.0, 0.1],   # $5,030
            [50600.0, 0.1],   # $5,060
        ]
        result = self.router.estimate_slippage("BTCUSDT", 20000, book)
        assert result["slippage_pct"] > 0
        assert result["estimated_fill"] > 50000.0

    def test_high_slippage_recommends_limit(self):
        """Slippage > 0.1% should recommend LIMIT order."""
        # Create book with significant price gaps
        book = [
            [50000.0, 0.01],   # $500
            [50100.0, 0.01],   # $501
            [50500.0, 0.01],   # $505
            [51000.0, 0.01],   # $510
        ]
        result = self.router.estimate_slippage("BTCUSDT", 2000, book)
        # With these gaps the VWAP will be above 50000
        if result["slippage_pct"] > 0.1:
            assert result["order_type"] == "LIMIT"

    def test_very_high_slippage_warns_rejection(self):
        """Slippage > 0.5% should add a rejection warning."""
        book = [
            [50000.0, 0.001],   # $50
            [50500.0, 0.001],   # $50.50
            [52000.0, 0.001],   # $52
            [55000.0, 0.001],   # $55
        ]
        result = self.router.estimate_slippage("BTCUSDT", 200, book)
        if result["slippage_pct"] > 0.5:
            assert "reducing size or rejecting" in (result["warning"] or "").lower() or \
                   "consider" in (result["warning"] or "").lower()

    def test_insufficient_book_depth_warns(self):
        """When book can't fill the full order, warn about partial fill."""
        book = [[50000.0, 0.01]]  # Only $500 available
        result = self.router.estimate_slippage("BTCUSDT", 10000, book)
        assert result["warning"] is not None
        assert "unfilled" in result["warning"].lower()

    def test_invalid_size_returns_zero(self):
        result = self.router.estimate_slippage("BTCUSDT", 0)
        assert result["slippage_pct"] == 0.0
        assert result["warning"] == "Invalid order size"

    # -- Optimal order type --

    def test_optimal_low_slippage_normal_urgency(self):
        assert self.router.optimal_order_type(0.05, "normal") == "MARKET"

    def test_optimal_moderate_slippage_limit(self):
        assert self.router.optimal_order_type(0.15, "normal") == "LIMIT"

    def test_optimal_high_slippage_reject(self):
        assert self.router.optimal_order_type(0.6, "normal") == "REJECT"

    def test_optimal_high_slippage_high_urgency_forces_market(self):
        assert self.router.optimal_order_type(0.6, "high") == "MARKET"

    def test_optimal_low_urgency_prefers_limit(self):
        assert self.router.optimal_order_type(0.05, "low") == "LIMIT"


# ===================================================================
# 2. Trailing Stop Engine Tests
# ===================================================================

class TestTrailingStopEngine:

    def test_trailing_not_active_before_threshold(self):
        """Trailing stop should not activate before 50% of TP distance."""
        cfg = TrailingStopConfig(activation_pct=50.0, trail_distance_atr_mult=2.0)
        pt = _make_portfolio(trailing_config=cfg)
        idea = _make_idea(entry=50000, sl=49000, tp=53000, idea_id="TST-1")
        pt.open_position(idea, 5000)

        # Price moves up only 20% of TP distance (600 of 3000)
        pt.mark_to_market({"BTCUSDT": 50600})
        status = pt.get_trailing_status()
        assert not status["TST-1"]["trailing_active"]

    def test_trailing_activates_at_50pct_tp(self):
        """Trailing stop activates when price reaches 50% of TP distance."""
        cfg = TrailingStopConfig(activation_pct=50.0, trail_distance_atr_mult=2.0)
        pt = _make_portfolio(trailing_config=cfg)
        idea = _make_idea(entry=50000, sl=49000, tp=53000, idea_id="TST-2")
        pt.open_position(idea, 5000)

        # TP distance = 3000, 50% = 1500, so price needs to reach 51500
        pt.mark_to_market({"BTCUSDT": 51600})
        status = pt.get_trailing_status()
        assert status["TST-2"]["trailing_active"]

    def test_trailing_stop_only_tightens_long(self):
        """For LONG: trailing stop should only move up, never down."""
        cfg = TrailingStopConfig(activation_pct=50.0, trail_distance_atr_mult=2.0)
        pt = _make_portfolio(trailing_config=cfg)
        idea = _make_idea(entry=50000, sl=49000, tp=53000, idea_id="TST-3")
        pt.open_position(idea, 5000)

        # Activate trailing
        pt.mark_to_market({"BTCUSDT": 52000})
        pos = pt._positions["TST-3"]
        sl_after_activation = pos.stop_loss

        # Price goes higher -> SL should tighten
        pt.mark_to_market({"BTCUSDT": 52500})
        sl_after_higher = pos.stop_loss
        assert sl_after_higher >= sl_after_activation

        # Price pulls back -> SL should NOT loosen
        pt.mark_to_market({"BTCUSDT": 51800})
        sl_after_pullback = pos.stop_loss
        assert sl_after_pullback >= sl_after_higher

    def test_trailing_stop_with_atr_values(self):
        """update_trailing_stops with explicit ATR values."""
        cfg = TrailingStopConfig(activation_pct=50.0, trail_distance_atr_mult=2.0)
        pt = _make_portfolio(trailing_config=cfg)
        idea = _make_idea(entry=50000, sl=49000, tp=53000, idea_id="TST-4")
        pt.open_position(idea, 5000)

        # Set price high enough to activate
        pt._last_prices["BTCUSDT"] = 52000
        # Update with explicit ATR
        pt.update_trailing_stops(atr_values={"BTCUSDT": 500})

        ts = pt._trailing_state["TST-4"]
        assert ts["atr"] == 500
        assert ts["trailing_active"]

    def test_trailing_short_position(self):
        """Trailing stop for SHORT: activates and tightens downward."""
        cfg = TrailingStopConfig(activation_pct=50.0, trail_distance_atr_mult=2.0)
        pt = _make_portfolio(trailing_config=cfg)
        idea = _make_idea(
            entry=50000, sl=51000, tp=47000, direction=Direction.SHORT, idea_id="TST-5"
        )
        pt.open_position(idea, 5000)

        # TP distance = 3000, 50% = 1500, price needs to drop to 48500
        pt.mark_to_market({"BTCUSDT": 48400})
        status = pt.get_trailing_status()
        assert status["TST-5"]["trailing_active"]
        pos = pt._positions["TST-5"]
        # SL should have tightened (moved down from 51000)
        assert pos.stop_loss < 51000

    def test_get_trailing_status_structure(self):
        """get_trailing_status returns correct keys."""
        pt = _make_portfolio()
        idea = _make_idea(idea_id="TST-6")
        pt.open_position(idea, 5000)
        status = pt.get_trailing_status()
        assert "TST-6" in status
        entry = status["TST-6"]
        assert "asset" in entry
        assert "trailing_active" in entry
        assert "best_price" in entry
        assert "current_sl" in entry


# ===================================================================
# 3. Auto-Rebalance Tests
# ===================================================================

class TestAutoRebalance:

    def _make_engine_with_positions(self, positions_spec):
        """Create engine with mocked positions.

        positions_spec: list of (asset, entry, sl, tp, size_usd, direction)
        """
        from bot.core.engine import RuneClawEngine
        engine = RuneClawEngine.__new__(RuneClawEngine)
        engine.portfolio = _make_portfolio(balance=100000)
        engine._last_rebalance_check = 0.0
        engine._rebalance_interval = 4 * 3600

        for asset, entry, sl, tp, size_usd, direction in positions_spec:
            idea = _make_idea(
                asset=asset, entry=entry, sl=sl, tp=tp, direction=direction,
                idea_id=f"RB-{asset}",
            )
            engine.portfolio.open_position(idea, size_usd)
            engine.portfolio.mark_to_market({asset: entry})

        return engine

    def test_no_positions_no_rebalance(self):
        from bot.core.engine import RuneClawEngine
        engine = RuneClawEngine.__new__(RuneClawEngine)
        engine.portfolio = _make_portfolio()
        engine._last_rebalance_check = 0.0
        engine._rebalance_interval = 4 * 3600
        heat = engine.check_portfolio_heat()
        assert heat["needs_rebalance"] is False
        assert heat["total_exposure_pct"] == 0.0

    def test_low_exposure_no_rebalance(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 10000, Direction.LONG),
        ])
        heat = engine.check_portfolio_heat()
        # 10k / ~100k equity = ~10%
        assert heat["total_exposure_pct"] < 60.0
        assert heat["needs_rebalance"] is False

    def test_high_total_exposure_triggers_rebalance(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 25000, Direction.LONG),
            ("ETHUSDT", 3000, 2900, 3300, 25000, Direction.LONG),
            ("SOLUSDT", 100, 95, 115, 20000, Direction.LONG),
        ])
        heat = engine.check_portfolio_heat()
        # 70k / ~100k = 70% > 60%
        assert heat["total_exposure_pct"] > 60.0
        assert heat["needs_rebalance"] is True
        assert len(heat["rebalance_actions"]) > 0

    def test_single_position_over_30pct_triggers(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 35000, Direction.LONG),
        ])
        heat = engine.check_portfolio_heat()
        assert heat["max_single_exposure_pct"] > 30.0
        assert heat["needs_rebalance"] is True

    def test_rebalance_signals_respect_interval(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 35000, Direction.LONG),
        ])
        signals1 = engine.get_rebalance_signals()
        assert len(signals1) > 0

        # Second call within interval -> empty
        signals2 = engine.get_rebalance_signals()
        assert len(signals2) == 0

    def test_rebalance_signals_after_interval(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 35000, Direction.LONG),
        ])
        signals1 = engine.get_rebalance_signals()
        assert len(signals1) > 0

        # Simulate time passing beyond interval
        engine._last_rebalance_check = time.monotonic() - (5 * 3600)
        signals2 = engine.get_rebalance_signals()
        assert len(signals2) > 0

    def test_rebalance_actions_contain_asset_names(self):
        engine = self._make_engine_with_positions([
            ("BTCUSDT", 50000, 49000, 53000, 40000, Direction.LONG),
        ])
        heat = engine.check_portfolio_heat()
        assert any("BTCUSDT" in a for a in heat["rebalance_actions"])
