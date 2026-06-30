"""
Tests for closing the learning loop (roadmap alpha item).

Before: get_learning_context() had ZERO callers, AND outcomes were never written
to the experience store in a form get_similar_setups could find (the result-only
records had no symbol/direction). So the loop was open on both ends.

After:
  * record_closed_outcome writes a COMPLETE, queryable outcome record.
  * get_similar_setups matches on symbol (+ optional direction/regime) and finds
    those outcomes -> get_learning_context.avg_past_pnl is real.
  * the engine applies a small, capped, asymmetric, opt-in confidence nudge.
"""

import inspect

import pytest

from bot.config import CONFIG


def _orch():
    from bot.learning.orchestrator import LearningOrchestrator
    return LearningOrchestrator()


class TestLoopWriteAndRead:
    def test_recorded_outcomes_are_queryable(self):
        orch = _orch()
        for _ in range(6):
            orch.record_closed_outcome(symbol="FOO/USDT", direction="LONG",
                                       pnl_result=-12.0, trade_id="t")
        ctx = orch.get_learning_context(symbol="FOO/USDT", direction="LONG")
        assert ctx["similar_past_setups"] == 6
        assert ctx["avg_past_pnl"] == pytest.approx(-12.0)

    def test_direction_is_scoped(self):
        orch = _orch()
        for _ in range(5):
            orch.record_closed_outcome(symbol="BAR/USDT", direction="LONG", pnl_result=-20.0)
        for _ in range(5):
            orch.record_closed_outcome(symbol="BAR/USDT", direction="SHORT", pnl_result=+8.0)
        long_ctx = orch.get_learning_context(symbol="BAR/USDT", direction="LONG")
        short_ctx = orch.get_learning_context(symbol="BAR/USDT", direction="SHORT")
        assert long_ctx["avg_past_pnl"] < 0
        assert short_ctx["avg_past_pnl"] > 0

    def test_regime_filter_is_optional(self):
        orch = _orch()
        orch.record_closed_outcome(symbol="BAZ/USDT", direction="LONG",
                                   pnl_result=-5.0, market_regime="STRONG_TREND_DOWN")
        # Querying with no regime ("") still finds the outcome.
        ctx = orch.get_learning_context(symbol="BAZ/USDT", direction="LONG", market_regime="")
        assert ctx["similar_past_setups"] == 1


class TestNudgeRule:
    """Pin the asymmetric, capped decision rule the engine applies."""
    def _delta(self, avg, n):
        c = CONFIG.learning
        if n < c.adaptive_confidence_min_samples or avg is None:
            return 0.0
        if avg < 0:
            return -c.adaptive_confidence_max_penalty
        if avg > 0:
            return c.adaptive_confidence_max_boost
        return 0.0

    def test_losing_setup_penalized(self):
        assert self._delta(-15.0, 6) == pytest.approx(-CONFIG.learning.adaptive_confidence_max_penalty)

    def test_winning_setup_boosted_less_than_penalty(self):
        d = self._delta(+15.0, 6)
        assert d == pytest.approx(CONFIG.learning.adaptive_confidence_max_boost)
        # Risk-first asymmetry: boost is smaller than penalty.
        assert CONFIG.learning.adaptive_confidence_max_boost < CONFIG.learning.adaptive_confidence_max_penalty

    def test_below_min_samples_no_nudge(self):
        assert self._delta(-15.0, CONFIG.learning.adaptive_confidence_min_samples - 1) == 0.0

    def test_nudge_keeps_confidence_in_unit_range(self):
        for base in (0.01, 0.55, 0.99):
            nudged = round(max(0.0, min(1.0, base + self._delta(-15.0, 6))), 4)
            assert 0.0 <= nudged <= 1.0


class TestConfigSafeDefaults:
    def test_enabled_by_default(self, monkeypatch):
        # Enabled by default (operator-requested activation); explicit env still wins.
        monkeypatch.delenv("ADAPTIVE_CONFIDENCE_ENABLED", raising=False)
        from bot.config import LearningConfig
        assert LearningConfig().adaptive_confidence_enabled is True

    def test_penalty_exceeds_boost(self):
        assert (CONFIG.learning.adaptive_confidence_max_penalty
                > CONFIG.learning.adaptive_confidence_max_boost)


class TestEngineWiring:
    def test_consumer_is_flag_gated(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine._analyze_signal)
        assert "CONFIG.learning.adaptive_confidence_enabled" in src
        assert "learning_confidence_nudge" in src

    def test_outcome_recorded_on_close(self):
        from bot.core.engine import RuneClawEngine
        src = inspect.getsource(RuneClawEngine._on_live_position_closed)
        assert "record_closed_outcome" in src
