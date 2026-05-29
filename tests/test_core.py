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
import pytest
import numpy as np
from datetime import datetime, timedelta

from bot.utils.models import (
    Direction, MarketSignal, RiskCheck, RiskVerdict,
    TradeExecution, TradeIdea, TradeStatus, PortfolioState,
)
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine
from bot.core.analyzer import Analyzer, Regime, _compute_adx, _ema
from bot.backtest.models import BacktestBar, BacktestConfig
from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine


# ── Fixtures ─────────────────────────────────────────────────────

def _make_idea(
    asset: str = "BTC/USDT",
    direction: Direction = Direction.LONG,
    entry: float = 65000.0,
    sl: float = 63700.0,
    tp: float = 66950.0,
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
        idea = _make_idea(entry=50000)
        trade = port.open_position(idea, 200)
        assert trade.trade_id == "TI-test001"
        assert trade.quantity == pytest.approx(0.004, abs=1e-6)
        assert port.balance == pytest.approx(9800, abs=0.01)
        assert len(port.open_positions) == 1

    def test_close_position_long_profit(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000)
        port.open_position(idea, 200)
        closed = port.close_position("TI-test001", 55000)
        assert closed is not None
        assert closed.pnl > 0
        # PnL = (55000 - 50000) * 0.004 = 20
        assert closed.pnl == pytest.approx(20.0, abs=0.1)
        assert port.balance == pytest.approx(10020.0, abs=0.1)

    def test_close_position_long_loss(self):
        port = _make_portfolio(10000)
        idea = _make_idea(entry=50000)
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
        idea = _make_idea(entry=50000)
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
        idea = _make_idea(entry=50000)
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
        result = asyncio.get_event_loop().run_until_complete(engine.run(bars))
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
        result = asyncio.get_event_loop().run_until_complete(engine.run(bars))
        if result.total_trades > 0:
            assert result.total_commission > 0

    def test_backtest_slippage_deducted(self):
        config = BacktestConfig(
            symbol="BTC/USDT", timeframe="1h",
            commission_pct=0.0, slippage_pct=0.1,
        )
        bars = DataLoader.generate_synthetic(bars=400, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.get_event_loop().run_until_complete(engine.run(bars))
        if result.total_trades > 0:
            assert result.total_slippage > 0

    def test_backtest_equity_curve_monotonic_timestamps(self):
        config = BacktestConfig(symbol="BTC/USDT", timeframe="1h")
        bars = DataLoader.generate_synthetic(bars=300, seed=42)
        engine = BacktestEngine(config)
        result = asyncio.get_event_loop().run_until_complete(engine.run(bars))
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
        idea = _make_idea(entry=100, sl=100, tp=110)
        assert idea.risk_reward_ratio == 0.0

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
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea(stop_loss=50000)
        check = risk.evaluate(idea)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("STOP_LOSS" in f for f in check.checks_failed)

    def test_stale_data_rejects_old_idea(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        # Manually set timestamp to 10 minutes ago
        old_ts = datetime.utcnow() - timedelta(seconds=600)
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
        # ATR of 5000 on a 50000 entry = 10%, above 3% default guard
        check = risk.evaluate(idea, atr=5000)
        assert check.verdict == RiskVerdict.REJECTED
        assert any("VOLATILITY" in f for f in check.checks_failed)

    def test_volatility_guard_passes_low_atr(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        # ATR of 500 on 50000 = 1%, below 3% guard
        check = risk.evaluate(idea, atr=500)
        assert not any("VOLATILITY" in f for f in check.checks_failed)

    def test_stats_tracking(self):
        port = _make_portfolio()
        risk = _make_risk(port)
        idea = self._make_idea()
        risk.evaluate(idea)
        stats = risk.stats
        assert stats["total_checks"] == 1


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
