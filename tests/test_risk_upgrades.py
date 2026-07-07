"""
Tests for the 4 risk engine upgrades:
  1. Adaptive Position Sizing (Kelly Criterion)
  2. Multi-Timeframe Confirmation
  3. Regime-Aware Risk Parameters
  4. Correlation-Weighted Portfolio Risk (PCA)
"""

import os
import tempfile
import pytest
from datetime import UTC, datetime

from bot.utils.models import Direction, TradeIdea
from bot.risk.portfolio import PortfolioTracker
from bot.risk.risk_engine import RiskEngine


# ── Fixtures ─────────────────────────────────────────────────────

def _make_engine(balance: float = 10_000.0) -> RiskEngine:
    """Create a RiskEngine with a fresh portfolio and no persisted state."""
    state_file = os.path.join(tempfile.mkdtemp(), "risk_state.json")
    portfolio = PortfolioTracker(initial_balance=balance)
    return RiskEngine(portfolio, state_file=state_file)


def _make_idea(**kwargs) -> TradeIdea:
    defaults = dict(
        asset="BTC/USDT",
        direction=Direction.LONG,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=115.0,
        confidence=0.75,
        reasoning="test idea",
        source="test",
    )
    defaults.update(kwargs)
    return TradeIdea(**defaults)


# ══════════════════════════════════════════════════════════════════
# Feature #1: Kelly Criterion Position Sizing
# ══════════════════════════════════════════════════════════════════

class TestKellyPositionSize:

    def test_zero_win_rate(self):
        """Zero win rate should return 0 — no edge."""
        result = RiskEngine.kelly_position_size(0.8, 0.0, 1.0, 1.0)
        assert result == 0.0

    def test_perfect_win_rate(self):
        """100% win rate should still be capped."""
        result = RiskEngine.kelly_position_size(0.8, 1.0, 1.0, 1.0)
        assert result > 0.0
        from bot.config import CONFIG
        assert result <= CONFIG.risk.max_position_pct / 100.0

    def test_negative_edge(self):
        """When expected value is negative, should return 0."""
        # win_rate=0.3, avg_win=1, avg_loss=2 => b=0.5, kelly = (0.3*0.5 - 0.7)/0.5 = -1.1
        result = RiskEngine.kelly_position_size(0.8, 0.3, 1.0, 2.0)
        assert result == 0.0

    def test_positive_edge(self):
        """Good edge should return a positive fraction."""
        # win_rate=0.6, avg_win=2, avg_loss=1 => b=2, kelly = (0.6*2 - 0.4)/2 = 0.4
        # half-kelly = 0.2 * confidence(0.8) = 0.16
        result = RiskEngine.kelly_position_size(0.8, 0.6, 2.0, 1.0)
        assert 0.0 < result <= 0.20

    def test_capped_at_max_position_pct(self):
        """Even with extreme edge, result must not exceed config cap."""
        from bot.config import CONFIG
        cap = CONFIG.risk.max_position_pct / 100.0
        result = RiskEngine.kelly_position_size(1.0, 0.99, 100.0, 0.01)
        assert result <= cap + 1e-9

    def test_zero_avg_win(self):
        """Zero avg_win should return 0."""
        assert RiskEngine.kelly_position_size(0.8, 0.5, 0.0, 1.0) == 0.0

    def test_zero_avg_loss(self):
        """Zero avg_loss should return 0."""
        assert RiskEngine.kelly_position_size(0.8, 0.5, 1.0, 0.0) == 0.0

    def test_get_recommended_size_insufficient_history(self):
        """With < 10 trades, should fall back to fixed-fractional."""
        engine = _make_engine(10_000.0)
        idea = _make_idea()
        size = engine.get_recommended_size(idea)
        # Should be equity * max_position_pct / 100
        from bot.config import CONFIG
        expected = 10_000.0 * (CONFIG.risk.max_position_pct / 100.0)
        assert abs(size - expected) < 1.0


# ══════════════════════════════════════════════════════════════════
# Feature #2: Multi-Timeframe Confirmation
# ══════════════════════════════════════════════════════════════════

class TestMultiTimeframeAlignment:

    def test_all_up(self):
        """All timeframes UP should be aligned."""
        aligned, reason = RiskEngine.check_timeframe_alignment(
            {"1h": "UP", "4h": "UP", "1d": "UP"}
        )
        assert aligned is True
        assert "UP" in reason

    def test_all_down(self):
        """All timeframes DOWN should be aligned."""
        aligned, reason = RiskEngine.check_timeframe_alignment(
            {"1h": "DOWN", "4h": "DOWN", "1d": "DOWN"}
        )
        assert aligned is True
        assert "DOWN" in reason

    def test_two_of_three_aligned(self):
        """2-of-3 alignment should pass."""
        aligned, reason = RiskEngine.check_timeframe_alignment(
            {"1h": "UP", "4h": "UP", "1d": "DOWN"}
        )
        assert aligned is True

    def test_no_alignment(self):
        """All different — should fail."""
        aligned, reason = RiskEngine.check_timeframe_alignment(
            {"1h": "UP", "4h": "DOWN", "1d": "SIDEWAYS"}
        )
        assert aligned is False
        assert "no alignment" in reason

    def test_insufficient_timeframes(self):
        """Fewer than 2 timeframes should return aligned (skip)."""
        aligned, reason = RiskEngine.check_timeframe_alignment({"1h": "UP"})
        assert aligned is True
        assert "insufficient" in reason

    def test_empty_trends(self):
        """Empty dict should return aligned (skip)."""
        aligned, reason = RiskEngine.check_timeframe_alignment({})
        assert aligned is True

    def test_mtf_check_graceful_skip(self):
        """_check_mtf_alignment should return None when no MTF signals present."""
        engine = _make_engine()
        idea = _make_idea(signals_used=["RSI_OVERSOLD", "MACD_CROSS"])
        result = engine._check_mtf_alignment(idea)
        assert result is None  # graceful skip

    def test_mtf_check_disabled_by_default_is_skip(self):
        """The MTF gate defaults OFF: even a counter-trend idea passes (the gate
        used to be dead — nothing produced the MTF: tags it parsed — so OFF must
        stay byte-identical to that always-skip behaviour)."""
        engine = _make_engine()
        idea = _make_idea(  # LONG into two bearish HTFs
            direction=Direction.LONG,
            signals_used=["MTF:4h=DOWN", "MTF:1d=DOWN"],
        )
        assert engine._check_mtf_alignment(idea) is None

    def test_mtf_check_with_signals(self):
        """Enabled + with-trend (legacy MTF: fallback): 2-of-3 UP → bullish HTF,
        a LONG is aligned → no rejection."""
        from bot.config import CONFIG
        engine = _make_engine()
        idea = _make_idea(
            direction=Direction.LONG,
            signals_used=["MTF:1h=UP", "MTF:4h=UP", "MTF:1d=DOWN"],
        )
        old = CONFIG.risk.mtf_alignment_gate_enabled
        object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", True)
        try:
            assert engine._check_mtf_alignment(idea) is None
        finally:
            object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", old)

    def test_mtf_check_misaligned(self):
        """Enabled + counter-trend: a LONG into a bearish HTF (2-of-3 DOWN via
        the legacy MTF: fallback) is rejected."""
        from bot.config import CONFIG
        engine = _make_engine()
        idea = _make_idea(
            direction=Direction.LONG,
            signals_used=["MTF:1h=UP", "MTF:4h=DOWN", "MTF:1d=DOWN"],
        )
        old = CONFIG.risk.mtf_alignment_gate_enabled
        object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", True)
        try:
            result = engine._check_mtf_alignment(idea)
        finally:
            object.__setattr__(CONFIG.risk, "mtf_alignment_gate_enabled", old)
        assert result is not None
        assert "MTF_ALIGNMENT" in result


# ══════════════════════════════════════════════════════════════════
# Feature #3: Regime-Aware Risk Parameters
# ══════════════════════════════════════════════════════════════════

class TestRegimeAdjustedParams:

    def test_choppy_regime(self):
        engine = _make_engine()
        # get_regime_adjusted_params is now pure (read-only); the current regime
        # is tracked via set_regime (called by the scan/analyze pipeline).
        engine.set_regime("CHOPPY", "NORMAL")
        params = engine.get_regime_adjusted_params("CHOPPY", "NORMAL")
        assert params["position_size_mult"] == 0.5
        assert params["cooldown_mult"] == 2.0
        assert engine._current_regime == "CHOPPY"

    def test_strong_trend_up(self):
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("STRONG_TREND_UP", "NORMAL")
        assert params["position_size_mult"] == 1.5
        assert params["cooldown_mult"] == 0.5

    def test_strong_trend_down(self):
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("STRONG_TREND_DOWN", "NORMAL")
        assert params["position_size_mult"] == 1.5
        assert params["cooldown_mult"] == 0.5

    def test_high_volatility_regime(self):
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("HIGH_VOLATILITY", "HIGH")
        assert params["position_size_mult"] == 0.3
        assert params["stop_width_mult"] == 1.5

    def test_ranging_regime(self):
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("RANGING", "NORMAL")
        assert params["position_size_mult"] == 0.7
        assert params["cooldown_mult"] == 1.5

    def test_unknown_regime_defaults(self):
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("UNKNOWN_THING", "NORMAL")
        assert params["position_size_mult"] == 1.0
        assert params["cooldown_mult"] == 1.0
        assert params["stop_width_mult"] == 1.0

    def test_high_vol_overlay(self):
        """HIGH volatility on a non-HIGH_VOLATILITY regime should reduce size."""
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("RANGING", "HIGH")
        # 0.7 * 0.7 = 0.49
        assert abs(params["position_size_mult"] - 0.49) < 0.01
        assert params["stop_width_mult"] > 1.0

    def test_low_vol_tightens_stops(self):
        """LOW volatility should tighten stops."""
        engine = _make_engine()
        params = engine.get_regime_adjusted_params("RANGING", "LOW")
        assert params["stop_width_mult"] < 1.0

    def test_vol_state_stored(self):
        engine = _make_engine()
        # Volatility state is stored by set_regime, not the pure params getter.
        engine.set_regime("CHOPPY", "HIGH")
        assert engine._current_vol_state == "HIGH"


# ══════════════════════════════════════════════════════════════════
# Feature #4: Correlation-Weighted Portfolio Risk (PCA)
# ══════════════════════════════════════════════════════════════════

class TestPortfolioConcentration:

    def test_highly_correlated(self):
        """Identical return series should trigger concentration rejection."""
        series = [0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02]
        # All assets have identical returns => PC1 ~ 100%
        matrix = [series[:], series[:], series[:]]
        ok, reason = RiskEngine.check_portfolio_concentration(matrix)
        assert ok is False
        assert "70%" in reason

    def test_uncorrelated(self):
        """Uncorrelated (orthogonal-ish) returns should pass."""
        import math
        n = 20
        # Construct reasonably uncorrelated series
        a = [math.sin(i * 0.5) for i in range(n)]
        b = [math.cos(i * 0.7) for i in range(n)]
        c = [math.sin(i * 1.3 + 2.0) for i in range(n)]
        matrix = [a, b, c]
        ok, reason = RiskEngine.check_portfolio_concentration(matrix)
        assert ok is True
        assert "diversification OK" in reason

    def test_single_asset(self):
        """Single asset should pass (not applicable)."""
        ok, reason = RiskEngine.check_portfolio_concentration([[0.01, 0.02, -0.01]])
        assert ok is True
        assert "single asset" in reason

    def test_insufficient_periods(self):
        """Too few periods should pass (graceful skip)."""
        ok, reason = RiskEngine.check_portfolio_concentration([[0.01], [0.02]])
        assert ok is True
        assert "insufficient" in reason

    def test_two_uncorrelated_assets(self):
        """Two assets with different patterns should pass."""
        a = [0.01, -0.01, 0.02, -0.02, 0.01, -0.01, 0.03, -0.02, 0.01, -0.01]
        b = [-0.02, 0.03, -0.01, 0.01, -0.03, 0.02, -0.01, 0.01, -0.02, 0.03]
        ok, reason = RiskEngine.check_portfolio_concentration([a, b])
        assert ok is True
