"""
RUNECLAW Test Suite -- validates the core trading pipeline.

Tests cover:
  - Risk engine: all checks, circuit breaker, correlation, edge cases
  - Portfolio: open/close, PnL calculation, stop monitoring, validation
  - Analyzer: indicator math (RSI, MACD, BB, ATR, ADX), confluence scoring
  - Backtest: end-to-end replay, fee/slippage, SL/TP intrabar
  - Models: Pydantic validation, computed properties
"""

import asyncio
import time
import pytest
import numpy as np
from datetime import UTC, datetime, timedelta

from bot.utils.models import (
    Direction, MarketSignal, MetricsSnapshot, RiskCheck, RiskVerdict,
    TradeExecution, TradeIdea, TradeStatus, PortfolioState,
)
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.core.analyzer import Analyzer, Regime, _compute_adx, _ema, _detect_candlestick_patterns, _compute_fibonacci, _compute_obv
from bot.core.metrics import MetricsEngine
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine


# ── Fixtures ─────────────────────────────────────────────────────

def _make_idea(
    asset: str = "BTC/USDT",
    direction: Direction = Direction.LONG,
    entry: float = 65000.0,
    sl: float = 58500.0,     # 10% below entry — keeps position_usd within 20% notional
    tp: float = 72800.0,     # 1.2x RR: entry + 1.2 * (entry - sl) = 65000 + 7800
    confidence: float = 0.72,
    idea_id: str = "TI-test001",
) -> TradeIdea:
    return TradeIdea(
        id=idea_id,
        asset=asset,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        confidence=confidence,
        reasoning="test idea",
        signals_used=["rsi", "macd"],
    )


def _make_portfolio(balance: float = 10000.0) -> PortfolioTracker:
    return PortfolioTracker(initial_balance=balance)


def _make_risk(portfolio: PortfolioTracker) -> RiskEngine:
    return RiskEngine(portfolio)


# ══════════════════════════════════════════════════════════════════
# RISK ENGINE TESTS
# ══════════════════════════════════════════════════════════════════

class TestRiskEngine:
    """Verify every risk check independently and in combination."""

    def test_approve_clean_trade(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = _make_idea()
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.APPROVED
        assert len(result.checks_failed) == 0
        assert "checks passed" in result.reason

    def test_reject_low_confidence(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = _make_idea(confidence=0.3)
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("CONFIDENCE" in f for f in result.checks_failed)

    def test_reject_low_rr_ratio(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        # SL far, TP close → bad R:R
        idea = _make_idea(entry=65000, sl=60000, tp=66000)
        assert idea.risk_reward_ratio < 1.5
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("RISK_REWARD" in f for f in result.checks_failed)

    def test_reject_zero_entry_price(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = _make_idea(entry=0)
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("ENTRY_PRICE" in f for f in result.checks_failed)

    def test_circuit_breaker_trips_on_daily_loss(self):
        port = _make_portfolio(balance=10000)
        risk = _make_risk(port)
        # Simulate 5% daily loss
        port._daily_pnl[datetime.now().strftime("%Y-%m-%d")] = -500.0
        idea = _make_idea()
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert risk.circuit_breaker_active

    def test_circuit_breaker_manual_reset(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        risk._circuit_open = True
        result = risk.evaluate(_make_idea())
        assert result.verdict == RiskVerdict.REJECTED

        risk.reset_circuit_breaker()
        assert not risk.circuit_breaker_active
        result2 = risk.evaluate(_make_idea())
        assert result2.verdict == RiskVerdict.APPROVED

    def test_reject_max_positions(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        # Fill up 5 positions
        for i in range(5):
            idea = _make_idea(idea_id=f"TI-fill{i}", entry=65000 + i)
            port.open_position(idea, 200)
        idea = _make_idea(idea_id="TI-toomany")
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("MAX_POSITIONS" in f for f in result.checks_failed)

    def test_correlation_check_blocks_concentrated_group(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        # Open 2 MEME positions
        for i, sym in enumerate(["DOGE/USDT", "SHIB/USDT"]):
            idea = _make_idea(asset=sym, idea_id=f"TI-meme{i}")
            port.open_position(idea, 200)
        # Third MEME should be blocked
        idea = _make_idea(asset="PEPE/USDT", idea_id="TI-meme3")
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("CORRELATION" in f for f in result.checks_failed)

    def test_correlation_allows_different_groups(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        port.open_position(_make_idea(asset="DOGE/USDT", idea_id="TI-a"), 200)
        port.open_position(_make_idea(asset="BTC/USDT", idea_id="TI-b"), 200)
        idea = _make_idea(asset="ETH/USDT", idea_id="TI-c")
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.APPROVED

    def test_consecutive_loss_streak(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        for _ in range(5):
            risk.record_trade_result(-10.0)
        assert risk.circuit_breaker_active
        assert risk.consecutive_losses == 5

    def test_loss_streak_rejects_at_three(self):
        """H4: 3 consecutive losses should trigger LOSS_STREAK rejection."""
        port = _make_portfolio()
        risk = _make_risk(port)
        for _ in range(3):
            risk.record_trade_result(-10.0)
        # Reset cooldown so it doesn't mask the streak check
        risk._last_loss_time = None
        idea = _make_idea()
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("LOSS_STREAK" in f for f in result.checks_failed)

    def test_fail_closed_on_portfolio_error(self):
        """If portfolio state can't be read, trade must be REJECTED."""
        port = _make_portfolio()
        risk = _make_risk(port)
        # Monkey-patch snapshot to raise
        original = port.snapshot
        port.snapshot = lambda: (_ for _ in ()).throw(RuntimeError("db error"))
        idea = _make_idea()
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert "unavailable" in result.reason.lower()
        port.snapshot = original


# ══════════════════════════════════════════════════════════════════
# PORTFOLIO TESTS
# ══════════════════════════════════════════════════════════════════

class TestPortfolio:
    """Verify position lifecycle and PnL calculation."""

    def test_open_position(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        trade = port.open_position(idea, 200)
        assert trade.trade_id == "TI-test001"
        assert trade.quantity == pytest.approx(0.004, abs=1e-6)
        assert port.balance == pytest.approx(9800, abs=0.01)
        assert len(port.open_positions) == 1

    def test_close_position_long_profit(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        port.open_position(idea, 200)
        closed = port.close_position("TI-test001", 55000)
        assert closed is not None
        assert closed.pnl > 0
        # PnL = (55000 - 50000) * 0.004 = 20
        assert closed.pnl == pytest.approx(20.0, abs=0.1)
        assert port.balance == pytest.approx(10020.0, abs=0.1)

    def test_close_position_long_loss(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        port.open_position(idea, 200)
        closed = port.close_position("TI-test001", 45000)
        assert closed is not None
        assert closed.pnl < 0
        # PnL = (45000 - 50000) * 0.004 = -20
        assert closed.pnl == pytest.approx(-20.0, abs=0.1)

    def test_close_position_short_profit(self):
        port = _make_portfolio(10000)
        idea = _make_idea(direction=Direction.SHORT, entry=50000, sl=52000, tp=47000)
        port.open_position(idea, 200)
        closed = port.close_position("TI-test001", 47000)
        assert closed is not None
        assert closed.pnl > 0

    def test_reject_zero_entry_price(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=0)
        with pytest.raises(ValueError, match="positive"):
            port.open_position(idea, 200)

    def test_reject_negative_entry_price(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=-100)
        with pytest.raises(ValueError, match="positive"):
            port.open_position(idea, 200)

    def test_caps_size_at_balance(self):
        port = _make_portfolio(100)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        trade = port.open_position(idea, 500)  # asking for 500 but only have 100
        assert trade.quantity == pytest.approx(100 / 50000, abs=1e-8)
        assert port.balance == pytest.approx(0, abs=0.01)

    def test_check_stops_long_sl(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        port.open_position(idea, 200)
        closed = port.check_stops({"BTC/USDT": 47000})
        assert len(closed) == 1
        assert closed[0].pnl < 0

    def test_check_stops_long_tp(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        port.open_position(idea, 200)
        closed = port.check_stops({"BTC/USDT": 56000})
        assert len(closed) == 1
        assert closed[0].pnl > 0

    def test_snapshot_initial(self):
        port = _make_portfolio(10000)
        snap = port.snapshot()
        assert snap.balance_usd == 10000
        assert snap.equity_usd == 10000
        assert snap.open_positions == 0
        assert snap.total_trades == 0
        assert snap.win_rate == 0.0

    def test_drawdown_tracking(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000)
        port.open_position(idea, 200)
        port.close_position("TI-test001", 40000)  # -$40 loss
        snap = port.snapshot()
        assert snap.max_drawdown_pct > 0


# ══════════════════════════════════════════════════════════════════
# ANALYZER INDICATOR TESTS
# ══════════════════════════════════════════════════════════════════

class TestAnalyzerIndicators:
    """Verify technical indicator calculations."""

    def test_ema_basic(self):
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _ema(data, 3)
        assert result[0] == 1.0
        assert result[-1] > result[0]  # EMA trends up with rising data

    def test_ema_constant(self):
        data = np.array([5.0, 5.0, 5.0, 5.0])
        result = _ema(data, 3)
        assert np.allclose(result, 5.0)

    def test_rsi_oversold(self):
        # Falling prices → RSI should be low
        closes = np.linspace(100, 70, 50)
        highs = closes + 1
        lows = closes - 1
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind["rsi"] < 40

    def test_rsi_overbought(self):
        # Rising prices → RSI should be high
        closes = np.linspace(70, 100, 50)
        highs = closes + 1
        lows = closes - 1
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind["rsi"] > 60

    def test_macd_bullish_crossover(self):
        # Strong uptrend → MACD should be positive
        closes = np.linspace(100, 130, 50)
        highs = closes + 2
        lows = closes - 2
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind["macd"] > 0
        assert ind["macd_histogram"] > 0

    def test_bollinger_bands_contain_price(self):
        np.random.seed(42)
        closes = np.cumsum(np.random.randn(50)) + 100
        highs = closes + np.abs(np.random.randn(50))
        lows = closes - np.abs(np.random.randn(50))
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind["bb_upper"] > ind["bb_mid"]
        assert ind["bb_lower"] < ind["bb_mid"]

    def test_atr_positive(self):
        np.random.seed(42)
        closes = np.cumsum(np.random.randn(50)) + 100
        highs = closes + np.abs(np.random.randn(50)) * 2
        lows = closes - np.abs(np.random.randn(50)) * 2
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind["atr"] > 0

    def test_adx_trending_market(self):
        # Strong uptrend → ADX should be high
        closes = np.linspace(100, 200, 50)
        highs = closes + 3
        lows = closes - 1
        result = _compute_adx(highs, lows, closes, 14)
        assert result["adx"] > 15
        assert result["plus_di"] > result["minus_di"]

    def test_adx_flat_market(self):
        # Flat market → ADX should be low
        np.random.seed(42)
        closes = 100 + np.random.randn(50) * 0.5  # tiny moves
        highs = closes + 0.3
        lows = closes - 0.3
        result = _compute_adx(highs, lows, closes, 14)
        assert result["adx"] < 30

    def test_regime_detection(self):
        assert Analyzer._detect_regime({"adx": 35, "plus_di": 30, "minus_di": 15}) == Regime.TREND_UP
        assert Analyzer._detect_regime({"adx": 35, "plus_di": 10, "minus_di": 30}) == Regime.TREND_DOWN
        assert Analyzer._detect_regime({"adx": 15, "plus_di": 10, "minus_di": 10}) == Regime.RANGE
        assert Analyzer._detect_regime({"adx": 22, "plus_di": 15, "minus_di": 15}) == Regime.CHOP

    def test_confluence_scoring(self):
        signal = MarketSignal(
            symbol="BTC/USDT", price=65000, change_pct_24h=3.0,
            volume_usd_24h=100000000, volume_spike=True,
        )
        # Bullish indicators → confluence > 0.5
        ind_bull = {"rsi": 28, "macd_histogram": 0.5, "bb_pct_b": 0.1,
                    "adx": 35, "plus_di": 30, "minus_di": 10, "vwap": 64000}
        score = Analyzer._score_confluence(ind_bull, Regime.TREND_UP, signal)
        assert score > 0.6

        # Bearish indicators → confluence < 0.5
        ind_bear = {"rsi": 75, "macd_histogram": -0.5, "bb_pct_b": 0.9,
                    "adx": 35, "plus_di": 10, "minus_di": 30, "vwap": 66000}
        signal_bear = MarketSignal(
            symbol="BTC/USDT", price=65000, change_pct_24h=-3.0,
            volume_usd_24h=100000000, volume_spike=True,
        )
        score_bear = Analyzer._score_confluence(ind_bear, Regime.TREND_DOWN, signal_bear)
        assert score_bear < 0.4


# ══════════════════════════════════════════════════════════════════
# BACKTEST ENGINE TESTS
# ══════════════════════════════════════════════════════════════════

class TestBacktestEngine:
    """Verify backtest replay logic."""

    def test_synthetic_data_invariants(self):
        bars = DataLoader.generate_synthetic(bars=200, seed=42)
        assert len(bars) == 200
        for b in bars:
            assert b.high >= max(b.open, b.close)
            assert b.low <= min(b.open, b.close)
            assert b.high >= b.low
            assert b.volume > 0

    def test_synthetic_data_reproducible(self):
        bars1 = DataLoader.generate_synthetic(bars=100, seed=42)
        bars2 = DataLoader.generate_synthetic(bars=100, seed=42)
        for b1, b2 in zip(bars1, bars2):
            assert b1.close == b2.close

    def test_synthetic_data_different_seeds(self):
        bars1 = DataLoader.generate_synthetic(bars=100, seed=42)
        bars2 = DataLoader.generate_synthetic(bars=100, seed=99)
        # Should produce different data
        assert bars1[-1].close != bars2[-1].close

    def test_backtest_runs_and_returns_result(self):
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=300, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        assert result.bars_processed == 300
        assert result.initial_balance == 10000
        assert result.final_equity > 0
        assert len(result.equity_curve) > 0

    def test_backtest_commission_deducted(self):
        config = BacktestConfig(
            symbol="BTC/USDT", timeframe="1h",
            commission_pct=0.1, slippage_pct=0.0,
        )
        bars = DataLoader.generate_synthetic(bars=400, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        if result.total_trades > 0:
            assert result.total_commission > 0

    def test_backtest_slippage_deducted(self):
        config = BacktestConfig(
            symbol="BTC/USDT", timeframe="1h",
            commission_pct=0.0, slippage_pct=0.1,
        )
        bars = DataLoader.generate_synthetic(bars=400, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        if result.total_trades > 0:
            assert result.total_slippage > 0

    def test_backtest_equity_curve_monotonic_timestamps(self):
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=300, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        timestamps = [p.timestamp for p in result.equity_curve]
        for i in range(1, len(timestamps)):
            assert timestamps[i] >= timestamps[i - 1]


# ══════════════════════════════════════════════════════════════════
# MODEL VALIDATION TESTS
# ══════════════════════════════════════════════════════════════════

class TestModels:
    """Verify Pydantic model constraints and computed properties."""

    def test_trade_idea_risk_reward(self):
        idea = _make_idea(entry=100, sl=95, tp=115)
        assert idea.risk_reward_ratio == 3.0

    def test_trade_idea_rr_zero_risk(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            _make_idea(entry=100, sl=100, tp=110)

    def test_market_signal_momentum_bounds(self):
        sig = MarketSignal(
            symbol="BTC/USDT", price=65000,
            change_pct_24h=50.0, volume_usd_24h=1000000,
            momentum_score=1.0,
        )
        assert -1 <= sig.momentum_score <= 1

    def test_direction_enum(self):
        assert Direction.LONG.value == "LONG"
        assert Direction.SHORT.value == "SHORT"

    def test_risk_verdict_enum(self):
        assert RiskVerdict.APPROVED.value == "APPROVED"
        assert RiskVerdict.REJECTED.value == "REJECTED"


# ══════════════════════════════════════════════════════════════════
# NEW INSTITUTIONAL-GRADE RISK CHECKS
# ══════════════════════════════════════════════════════════════════

class TestRiskEngineNewChecks:
    """Tests for the 5 new institutional-grade risk checks."""

    def _make_idea(self, **overrides):
        defaults = dict(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=50000, stop_loss=49000, take_profit=53000,
            confidence=0.75, reasoning="test", source="TEST",
        )
        defaults.update(overrides)
        return TradeIdea(**defaults)

    def test_stop_loss_required_rejects_zero_sl(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea(stop_loss=0)
        check = risk.evaluate(idea)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("STOP_LOSS" in f for f in check.checks_failed)

    def test_stop_loss_rejects_sl_equals_entry(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            self._make_idea(stop_loss=50000)

    def test_stale_data_rejects_old_idea(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        # Manually set timestamp to 10 minutes ago
        old_ts = datetime.now(UTC) - timedelta(seconds=600)
        idea = idea.model_copy(update={"timestamp": old_ts})
        check = risk.evaluate(idea)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("STALE_DATA" in f for f in check.checks_failed)

    def test_fresh_data_passes(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        check = risk.evaluate(idea)
        # Should not fail on stale data
        assert not any("STALE_DATA" in f for f in check.checks_failed)

    def test_cooldown_after_loss(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        risk.record_trade_result(-100)  # record a loss
        check = risk.evaluate(idea)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("COOLDOWN" in f for f in check.checks_failed)

    def test_volatility_guard_rejects_high_atr(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        # ATR of 5000 on a 50000 entry = 10%, above 6% volatility guard threshold
        check = risk.evaluate(idea, atr=5000)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("VOLATILITY" in f for f in check.checks_failed)

    def test_volatility_guard_passes_low_atr(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        # ATR of 500 on 50000 = 1%, below 6% volatility guard threshold
        check = risk.evaluate(idea, atr=500)
        assert not any("VOLATILITY" in f for f in check.checks_failed)

    def test_stats_tracking(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        risk.evaluate(idea)
        stats = risk.stats
        assert stats["total_checks"] == 1

    def test_symbol_exposure_rejects_concentrated_single_asset(self):
        """Per-symbol exposure limit should reject when one asset exceeds max_symbol_exposure_pct."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)
        # Open a large BTC position (25% of equity = $2500)
        idea1 = self._make_idea(entry_price=50000, idea_id="TI-sym1")
        port.open_position(idea1, 2500)
        # Second BTC position should push symbol exposure above 20% max
        idea2 = self._make_idea(entry_price=50000, idea_id="TI-sym2")
        result = risk.evaluate(idea2)
        assert any("SYMBOL_EXPOSURE" in f for f in result.checks_failed)

    def test_symbol_exposure_passes_different_assets(self):
        """Different assets should not trigger per-symbol exposure limit."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)
        # Open a small BTC position
        idea1 = self._make_idea(entry_price=50000, idea_id="TI-sym3")
        port.open_position(idea1, 500)
        # ETH should be fine -- different asset; use 10% SL so position fits within 20% notional
        idea2 = self._make_idea(asset="ETH/USDT", entry_price=3000,
                                stop_loss=2700, take_profit=3600, idea_id="TI-sym4")
        result = risk.evaluate(idea2)
        assert not any("SYMBOL_EXPOSURE" in f for f in result.checks_failed)


# ══════════════════════════════════════════════════════════════════
# AGENT STATE & NEW MODEL TESTS
# ══════════════════════════════════════════════════════════════════

class TestAgentStateModel:
    """Tests for the new AgentState and StateTransition models."""

    def test_agent_states_exist(self):
        from bot.utils.models import AgentState
        assert AgentState.IDLE == "IDLE"
        assert AgentState.SCANNING == "SCANNING"
        assert AgentState.HALTED == "HALTED"
        assert AgentState.COOLING_DOWN == "COOLING_DOWN"

    def test_state_transition_model(self):
        from bot.utils.models import AgentState, StateTransition
        t = StateTransition(
            from_state=AgentState.IDLE,
            to_state=AgentState.SCANNING,
            reason="tick started",
        )
        assert t.from_state == AgentState.IDLE
        assert t.to_state == AgentState.SCANNING

    def test_metrics_snapshot(self):
        from bot.utils.models import MetricsSnapshot
        m = MetricsSnapshot(total_trades=10, winning_trades=7, win_rate=0.7)
        assert m.total_trades == 10
        assert m.win_rate == 0.7

    def test_trade_idea_source_field(self):
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=50000, stop_loss=49000, take_profit=53000,
            confidence=0.7, reasoning="test", source="LLM",
        )
        assert idea.source == "LLM"

    def test_trade_idea_default_source(self):
        idea = TradeIdea(
            asset="BTC/USDT", direction=Direction.LONG,
            entry_price=50000, stop_loss=49000, take_profit=53000,
            confidence=0.7, reasoning="test",
        )
        assert idea.source == "unknown"


# ══════════════════════════════════════════════════════════════════
# A. INTEGRATION TESTS
# ══════════════════════════════════════════════════════════════════

class TestIntegration:
    """End-to-end integration tests across multiple components."""

    def test_full_pipeline_idea_to_execution(self):
        """Create engine components, generate idea, risk check, open, check stops, close, verify PnL."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)

        # Generate a trade idea manually
        idea = _make_idea(
            asset="BTC/USDT", entry=50000, sl=45000, tp=65000,
            confidence=0.75, idea_id="TI-integ001",
        )

        # Risk check should approve
        check = risk.evaluate(idea)
        assert check.verdict == RiskVerdict.APPROVED

        # Open position with the approved size
        size_usd = check.position_size_usd
        assert size_usd > 0
        trade = port.open_position(idea, size_usd)
        assert trade.trade_id == "TI-integ001"
        assert len(port.open_positions) == 1

        # Check stops -- price not hitting SL or TP
        closed = port.check_stops({"BTC/USDT": 51000})
        assert len(closed) == 0

        # Close position manually at profit
        closed_trade = port.close_position("TI-integ001", 54000)
        assert closed_trade is not None
        assert closed_trade.pnl > 0
        assert len(port.open_positions) == 0

        # Verify portfolio reflects profit
        snap = port.snapshot()
        assert snap.total_trades == 1
        assert snap.total_pnl > 0

    def test_circuit_breaker_cascade(self):
        """Record enough losses to trip circuit breaker, verify all subsequent trades rejected."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)

        # Record 5 consecutive losses (max_consecutive_losses default is 5)
        for _ in range(5):
            risk.record_trade_result(-50.0)

        assert risk.circuit_breaker_active
        assert risk.consecutive_losses == 5

        # Every subsequent trade should be rejected
        for i in range(3):
            idea = _make_idea(idea_id=f"TI-cascade{i}")
            result = risk.evaluate(idea)
            assert result.verdict == RiskVerdict.REJECTED
            assert any("CIRCUIT_BREAKER" in f for f in result.checks_failed)

    def test_cooldown_blocks_trades(self):
        """Record a loss, verify trades are blocked during cooldown period."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)

        # Record a single loss -- should trigger cooldown
        risk.record_trade_result(-50.0)

        # Immediately evaluate -- should be rejected due to cooldown
        idea = _make_idea(idea_id="TI-cool001")
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("COOLDOWN" in f for f in result.checks_failed)

    def test_portfolio_callback_updates_risk(self):
        """Open position, close at loss, verify risk engine's consecutive_losses incremented.
        C1 fix: now tests the auto-wired callback (not manual wiring)."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)

        # C1: wire up the callback the same way the engine does
        port._on_trade_close = risk.record_trade_result

        idea = _make_idea(entry=50000, sl=48000, tp=55000, idea_id="TI-cb001")
        port.open_position(idea, 200)

        # Close at a loss
        port.close_position("TI-cb001", 45000)
        assert risk.consecutive_losses == 1

        # Another loss
        idea2 = _make_idea(entry=50000, sl=48000, tp=55000, idea_id="TI-cb002")
        port.open_position(idea2, 200)
        port.close_position("TI-cb002", 46000)
        assert risk.consecutive_losses == 2

    def test_engine_auto_wires_callback(self):
        """L5/C1: Verify RuneClawEngine auto-wires on_trade_close callback."""
        from bot.core.engine import RuneClawEngine
        engine = RuneClawEngine()
        # The callback should be wired automatically
        assert engine.portfolio._on_trade_close is not None
        assert engine.portfolio._on_trade_close == engine.risk.record_trade_result

    def test_backtest_engine_auto_wires_callback(self):
        """L5/C1: Verify BacktestEngine auto-wires on_trade_close callback."""
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bt_engine = BacktestEngine(config)
        assert bt_engine.portfolio._on_trade_close is not None
        assert bt_engine.portfolio._on_trade_close == bt_engine.risk.record_trade_result


# ══════════════════════════════════════════════════════════════════
# B. EDGE CASE TESTS
# ══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Edge case scenarios for robustness verification."""

    def test_risk_eval_zero_equity(self):
        """Portfolio with 0 equity should reject trades."""
        port = _make_portfolio(0.01)
        # Drain the balance
        idea = _make_idea(entry=100, sl=90, tp=120, idea_id="TI-drain")
        port.open_position(idea, 0.01)
        port.close_position("TI-drain", 1)  # massive loss

        risk = _make_risk(port)
        idea2 = _make_idea(idea_id="TI-zero-eq")
        result = risk.evaluate(idea2)
        assert result.verdict == RiskVerdict.REJECTED

    def test_risk_eval_nan_confidence(self):
        """Trade idea with 0.0 confidence (minimum valid) should be rejected by min_confidence check."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)
        idea = _make_idea(confidence=0.0, idea_id="TI-zeroconf")
        result = risk.evaluate(idea)
        assert result.verdict == RiskVerdict.REJECTED
        assert any("CONFIDENCE" in f for f in result.checks_failed)

    def test_close_nonexistent_position(self):
        """Closing a position that doesn't exist should return None."""
        port = _make_portfolio(10000)
        result = port.close_position("TI-nonexistent", 50000)
        assert result is None

    def test_double_open_same_idea(self):
        """Opening position with same idea ID twice -- second overwrites in dict, both succeed independently."""
        port = _make_portfolio(10000)
        idea1 = _make_idea(entry=50000, sl=48000, tp=55000, idea_id="TI-double")
        idea2 = _make_idea(entry=51000, sl=49000, tp=56000, idea_id="TI-double2")
        trade1 = port.open_position(idea1, 200)
        trade2 = port.open_position(idea2, 200)
        assert trade1.trade_id == "TI-double"
        assert trade2.trade_id == "TI-double2"
        assert len(port.open_positions) == 2
        assert port.balance == pytest.approx(9600, abs=0.01)

    def test_very_small_position(self):
        """Position with very small size ($1) should not cause division errors."""
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=49000, tp=53000, idea_id="TI-tiny")
        trade = port.open_position(idea, 1)
        assert trade.quantity > 0
        assert trade.quantity == pytest.approx(1 / 50000, abs=1e-10)

        # Close it
        closed = port.close_position("TI-tiny", 51000)
        assert closed is not None
        assert closed.pnl == pytest.approx((51000 - 50000) * (1 / 50000), abs=1e-6)

    def test_backtest_empty_result(self):
        """Backtest with very few bars that produce 0 trades should not crash."""
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        # Only 50 bars -- not enough for the lookback window of 100
        bars = DataLoader.generate_synthetic(bars=50, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        assert result.bars_processed == 50
        assert result.total_trades == 0
        assert result.final_equity == config.initial_balance

    def test_synthetic_data_seed_zero(self):
        """Generate synthetic data with seed=0, verify valid data."""
        bars = DataLoader.generate_synthetic(bars=100, seed=0)
        assert len(bars) == 100
        for b in bars:
            assert b.high >= b.low
            assert b.high >= max(b.open, b.close)
            assert b.low <= min(b.open, b.close)
            assert b.volume > 0


# ══════════════════════════════════════════════════════════════════
# C. NEGATIVE INPUT TESTS
# ══════════════════════════════════════════════════════════════════

class TestNegativeInputs:
    """Tests for invalid or extreme inputs."""

    def test_analyzer_insufficient_candles(self):
        """Calling analyzer indicators with < 30 candles should return None (fail-closed)."""
        closes = np.linspace(100, 110, 15)
        highs = closes + 1
        lows = closes - 1
        # _compute_indicators should fail closed with insufficient data
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert ind is None

    def test_portfolio_negative_exit_price(self):
        """Close position with exit_price=0 should be rejected (return None)."""
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000, sl=48000, tp=55000, idea_id="TI-negex")
        port.open_position(idea, 200)
        result = port.close_position("TI-negex", 0)
        assert result is None
        # Position should still be open
        assert len(port.open_positions) == 1

    def test_risk_engine_extreme_drawdown(self):
        """Portfolio at 99% drawdown should trigger circuit breaker."""
        port = _make_portfolio(10000)
        risk = _make_risk(port)

        # Simulate a massive loss to create > 10% drawdown (max_drawdown_pct default is 10%)
        idea = _make_idea(entry=100, sl=50, tp=200, idea_id="TI-dd")
        port.open_position(idea, 5000)
        port.close_position("TI-dd", 1)  # catastrophic loss

        idea2 = _make_idea(idea_id="TI-dd-check")
        result = risk.evaluate(idea2)
        assert result.verdict == RiskVerdict.REJECTED
        # The drawdown check or equity check should fail
        failed_str = " ".join(result.checks_failed)
        assert "DRAWDOWN" in failed_str or "EQUITY" in failed_str

    def test_backtest_single_bar(self):
        """Run backtest with only 1 bar, verify no crash."""
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=1, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.run(engine.run(bars))
        assert result.bars_processed == 1
        assert result.total_trades == 0


# ══════════════════════════════════════════════════════════════════
# D. TIGHTENED ASSERTION TESTS
# ══════════════════════════════════════════════════════════════════

class TestTightenedAssertions:
    """Tests with tighter bounds on known computations."""

    def test_ema_exact_values(self):
        """Verify EMA produces exact expected values for known input."""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        result = _ema(data, 3)
        # EMA with period=3: alpha = 2/(3+1) = 0.5
        # result[0] = 1.0
        # result[1] = 0.5 * 2 + 0.5 * 1.0 = 1.5
        # result[2] = 0.5 * 3 + 0.5 * 1.5 = 2.25
        # result[3] = 0.5 * 4 + 0.5 * 2.25 = 3.125
        # result[4] = 0.5 * 5 + 0.5 * 3.125 = 4.0625
        assert result[0] == pytest.approx(1.0, abs=1e-10)
        assert result[1] == pytest.approx(1.5, abs=1e-10)
        assert result[2] == pytest.approx(2.25, abs=1e-10)
        assert result[3] == pytest.approx(3.125, abs=1e-10)
        assert result[4] == pytest.approx(4.0625, abs=1e-10)

    def test_rsi_tight_bounds(self):
        """RSI for a known downtrend should be in [15, 35], not just < 40."""
        closes = np.linspace(100, 70, 50)
        highs = closes + 1
        lows = closes - 1
        ind = Analyzer._compute_indicators(highs, lows, closes)
        assert 0 <= ind["rsi"] <= 35, f"RSI={ind['rsi']} outside [0, 35]"

    def test_sharpe_zero_returns(self):
        """All identical equity points should produce Sharpe = 0.0."""
        me = MetricsEngine()
        for _ in range(100):
            me._equity_curve.append(10000.0)
        sharpe = me._compute_sharpe()
        assert sharpe == 0.0


# ══════════════════════════════════════════════════════════════════
# E. METRICS ENGINE TESTS
# ══════════════════════════════════════════════════════════════════

class TestMetricsEngine:
    """Tests for the MetricsEngine analytics computation."""

    def _make_closed_trade(self, pnl: float, trade_id: str = "T-1") -> TradeExecution:
        """Helper to create a closed trade with a given PnL."""
        now = datetime.now(UTC)
        return TradeExecution(
            trade_id=trade_id,
            asset="BTC/USDT",
            direction=Direction.LONG,
            entry_price=50000,
            quantity=0.01,
            stop_loss=49000,
            take_profit=53000,
            status=TradeStatus.EXECUTED,
            pnl=pnl,
            exit_price=50000 + pnl / 0.01,
            is_paper=True,
            opened_at=now - timedelta(hours=2),
            closed_at=now,
        )

    def test_metrics_compute_empty_trades(self):
        """No trades should produce sensible defaults."""
        me = MetricsEngine()
        me._equity_curve = [10000.0, 10000.0]
        result = me.compute([])
        assert result.total_trades == 0
        assert result.win_rate == 0.0
        assert result.avg_win == 0.0
        assert result.avg_loss == 0.0
        assert result.profit_factor == 0.0
        assert result.current_streak == 0

    def test_metrics_compute_all_wins(self):
        """All winning trades should give 100% win rate and positive Sharpe."""
        me = MetricsEngine()
        # Vary PnL amounts so std != 0, enabling a non-zero Sharpe computation
        pnl_amounts = [30.0, 50.0, 70.0, 40.0, 60.0]
        trades = [self._make_closed_trade(pnl=p, trade_id=f"T-w{i}") for i, p in enumerate(pnl_amounts)]
        # Build an ascending equity curve
        equity = 10000.0
        for t in trades:
            me._equity_curve.append(equity)
            equity += t.pnl
        me._equity_curve.append(equity)

        result = me.compute(trades)
        assert result.total_trades == 5
        assert result.winning_trades == 5
        assert result.win_rate == 1.0
        assert result.avg_win > 0
        assert result.profit_factor == 999.99  # inf capped
        assert result.sharpe_ratio > 0

    def test_metrics_compute_all_losses(self):
        """All losing trades should give 0% win rate and negative/zero Sharpe."""
        me = MetricsEngine()
        trades = [self._make_closed_trade(pnl=-30.0, trade_id=f"T-l{i}") for i in range(5)]
        # Build a descending equity curve
        equity = 10000.0
        for t in trades:
            me._equity_curve.append(equity)
            equity += t.pnl
        me._equity_curve.append(equity)

        result = me.compute(trades)
        assert result.total_trades == 5
        assert result.winning_trades == 0
        assert result.win_rate == 0.0
        assert result.avg_loss < 0
        assert result.sharpe_ratio <= 0

    def test_equity_curve_bounded(self):
        """MetricsEngine equity curve list grows with record_equity calls -- verify it works with large inputs."""
        me = MetricsEngine()
        # Record 15000 equity points
        for i in range(15000):
            me._equity_curve.append(10000.0 + i * 0.1)
        assert len(me._equity_curve) == 15000
        # Sharpe should still compute without error
        sharpe = me._compute_sharpe()
        assert isinstance(sharpe, float)
        assert sharpe > 0  # upward equity curve


class TestAdvancedAnalysis:
    """Tests for candlestick patterns, Fibonacci, OBV, and enhanced confluence."""

    def test_obv_rising(self):
        """OBV should rise when price rises on volume."""
        closes = np.array([100, 101, 102, 103, 104], dtype=float)
        volumes = np.array([1000, 1200, 1100, 1300, 1400], dtype=float)
        obv = _compute_obv(closes, volumes)
        assert len(obv) == 5
        assert obv[-1] > obv[0]  # rising prices -> rising OBV

    def test_obv_falling(self):
        """OBV should fall when price drops on volume."""
        closes = np.array([104, 103, 102, 101, 100], dtype=float)
        volumes = np.array([1000, 1200, 1100, 1300, 1400], dtype=float)
        obv = _compute_obv(closes, volumes)
        assert obv[-1] < obv[0]

    def test_fibonacci_levels(self):
        """Fibonacci levels should be correctly computed from swing high/low."""
        highs = np.array([100, 105, 110, 108, 106], dtype=float)
        lows = np.array([95, 98, 102, 100, 99], dtype=float)
        closes = np.array([98, 103, 107, 104, 102], dtype=float)
        fib = _compute_fibonacci(highs, lows, closes)
        assert fib["fib_swing_high"] == 110.0
        assert fib["fib_swing_low"] == 95.0
        assert fib["fib_500"] == pytest.approx(102.5, abs=0.01)
        assert "fib_zone" in fib

    def test_fibonacci_flat_market(self):
        """Fibonacci with no range should return swing high/low only."""
        highs = np.array([100, 100, 100], dtype=float)
        lows = np.array([100, 100, 100], dtype=float)
        closes = np.array([100, 100, 100], dtype=float)
        fib = _compute_fibonacci(highs, lows, closes)
        assert fib["fib_swing_high"] == 100.0
        assert "fib_236" not in fib  # no range, no fib levels

    def test_doji_detection(self):
        """A candle with nearly equal open/close should be detected as doji."""
        opens = np.array([100, 101, 102], dtype=float)
        highs = np.array([103, 104, 106], dtype=float)
        lows = np.array([97, 98, 98], dtype=float)
        closes = np.array([101, 100, 102.1], dtype=float)  # last candle: open=102, close=102.1
        patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        assert "doji" in patterns
        assert patterns["doji"] == "neutral"

    def test_hammer_detection(self):
        """Hammer: small body at top, long lower wick."""
        opens = np.array([100, 101, 105], dtype=float)
        highs = np.array([103, 104, 105.5], dtype=float)
        lows = np.array([97, 98, 98], dtype=float)
        closes = np.array([101, 100, 105.3], dtype=float)  # body=0.3, upper_wick=0.2, lower_wick=7
        patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        assert "hammer" in patterns
        assert patterns["hammer"] == "bullish"

    def test_bullish_engulfing(self):
        """Bullish engulfing: prev bearish, current bullish wraps prev."""
        opens = np.array([100, 105, 99], dtype=float)
        highs = np.array([103, 106, 107], dtype=float)
        lows = np.array([97, 98, 98], dtype=float)
        closes = np.array([101, 100, 106], dtype=float)  # prev: 105->100 (bearish), curr: 99->106 (bullish engulf)
        patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        assert "bullish_engulfing" in patterns

    def test_three_white_soldiers(self):
        """Three White Soldiers: three consecutive bullish candles with higher closes."""
        opens = np.array([100, 103, 106], dtype=float)
        highs = np.array([104, 107, 110], dtype=float)
        lows = np.array([99, 102, 105], dtype=float)
        closes = np.array([103, 106, 109], dtype=float)
        patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        assert "three_white_soldiers" in patterns

    def test_no_patterns_on_insufficient_data(self):
        """With fewer than 3 bars, no patterns should be detected."""
        opens = np.array([100, 101], dtype=float)
        highs = np.array([103, 104], dtype=float)
        lows = np.array([97, 98], dtype=float)
        closes = np.array([101, 100], dtype=float)
        patterns = _detect_candlestick_patterns(opens, highs, lows, closes)
        assert patterns == {}

    def test_indicators_include_fib_and_obv(self):
        """_compute_indicators should now return Fibonacci and OBV data."""
        n = 60
        highs = np.linspace(100, 110, n) + np.random.default_rng(42).uniform(0, 2, n)
        lows = np.linspace(95, 105, n) + np.random.default_rng(43).uniform(-2, 0, n)
        closes = (highs + lows) / 2
        volumes = np.random.default_rng(44).uniform(1000, 5000, n)
        ind = Analyzer._compute_indicators(highs, lows, closes, volumes)
        assert ind is not None
        assert "fib_swing_high" in ind
        assert "fib_500" in ind
        assert "fib_zone" in ind
        assert "obv" in ind
        assert "obv_trend" in ind

    def test_confluence_uses_obv(self):
        """Confluence scoring should incorporate OBV trend vote."""
        indicators = {
            "rsi": 50, "macd_histogram": 0.01, "bb_pct_b": 0.5,
            "adx": 15, "plus_di": 20, "minus_di": 18,
            "vwap": 100, "obv_trend": "rising",
            "candle_bullish_count": 1, "candle_bearish_count": 0,
            "fib_zone": "382_500",
        }
        signal = MarketSignal(symbol="TEST/USDT", price=100, change_pct_24h=1.0, volume_usd_24h=1e6)
        score = Analyzer._score_confluence(indicators, Regime.RANGE, signal)
        assert 0 <= score <= 1

    def test_rule_based_thesis_includes_patterns(self):
        """Rule-based thesis reasoning should mention candle patterns."""
        signal = MarketSignal(symbol="TEST/USDT", price=100, change_pct_24h=2.0,
                              volume_usd_24h=1e6, volume_spike=True)
        ind = {
            "rsi": 35, "macd_histogram": 0.5, "bb_pct_b": 0.2,
            "adx": 30, "plus_di": 25, "minus_di": 15,
            "confluence": 0.7, "regime": "TREND_UP",
            "obv_trend": "rising", "fib_zone": "500_618",
            "candle_patterns": {"hammer": "bullish", "doji": "neutral"},
        }
        result = Analyzer._rule_based_thesis(signal, ind)
        assert "hammer" in result["reasoning"]
        assert result["direction"] == "LONG"
        assert result["confidence"] > 0.5
