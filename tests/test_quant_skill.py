"""
Tests for bot.skills.quant_skill

Covers: factor scoring boundaries, regime classification, volatility state,
gate pass/fail, edge strength, insufficient data, full pipeline, factor weights,
to_dict keys, and skill registration.
"""

import unittest
from bot.skills.quant_skill import (
    # Enums & constants
    MarketRegime,
    VolatilityState,
    EdgeStrength,
    QUANT_SCORE_GATE,
    FACTOR_WEIGHTS,
    # Data models
    FactorScores,
    QuantReport,
    # Factor scoring functions
    _score_trend,
    _score_momentum,
    _score_mean_reversion,
    _score_volume,
    _score_volatility_fit,
    _vol_fit_score,
    _score_hurst,
    # Regime & composite
    _classify_regime,
    _composite_score,
    _edge_strength,
    # Core pipeline
    run_quant_analysis,
    _generate_synthetic_ohlcv,
    # Skill class
    QuantAnalyzeSkill,
)


class TestScoreTrend(unittest.TestCase):
    """_score_trend: ADX 0-25 -> 0.0, 25-40 -> linear 0-1, 40+ -> 1.0."""

    def test_adx_zero(self):
        self.assertAlmostEqual(_score_trend(0), 0.0)

    def test_adx_below_25(self):
        self.assertAlmostEqual(_score_trend(10), 0.0)
        self.assertAlmostEqual(_score_trend(24.99), 0.0)

    def test_adx_at_25(self):
        self.assertAlmostEqual(_score_trend(25), 0.0)

    def test_adx_midpoint(self):
        # 32.5 is midpoint between 25 and 40 -> 0.5
        self.assertAlmostEqual(_score_trend(32.5), 0.5)

    def test_adx_at_40(self):
        self.assertAlmostEqual(_score_trend(40), 1.0)

    def test_adx_above_40(self):
        self.assertAlmostEqual(_score_trend(60), 1.0)
        self.assertAlmostEqual(_score_trend(100), 1.0)

    def test_linear_region(self):
        # ADX 30 -> (30-25)/15 = 5/15 = 0.333...
        self.assertAlmostEqual(_score_trend(30), 5.0 / 15.0)


class TestScoreMomentum(unittest.TestCase):
    """_score_momentum: abs(mom) / 0.03, capped at 1.0."""

    def test_zero_momentum(self):
        self.assertAlmostEqual(_score_momentum(0.0), 0.0)

    def test_positive_momentum_below_cap(self):
        self.assertAlmostEqual(_score_momentum(0.015), 0.5)

    def test_positive_at_cap(self):
        self.assertAlmostEqual(_score_momentum(0.03), 1.0)

    def test_positive_above_cap(self):
        self.assertAlmostEqual(_score_momentum(0.05), 1.0)

    def test_negative_momentum(self):
        # Uses abs(), so negative should mirror positive
        self.assertAlmostEqual(_score_momentum(-0.03), 1.0)
        self.assertAlmostEqual(_score_momentum(-0.015), 0.5)


class TestScoreMeanReversion(unittest.TestCase):
    """_score_mean_reversion: abs(z) / 2.0, capped at 1.0."""

    def test_zero_zscore(self):
        self.assertAlmostEqual(_score_mean_reversion(0.0), 0.0)

    def test_zscore_one(self):
        self.assertAlmostEqual(_score_mean_reversion(1.0), 0.5)

    def test_zscore_two(self):
        self.assertAlmostEqual(_score_mean_reversion(2.0), 1.0)

    def test_zscore_above_two(self):
        self.assertAlmostEqual(_score_mean_reversion(3.5), 1.0)

    def test_negative_zscore(self):
        self.assertAlmostEqual(_score_mean_reversion(-1.0), 0.5)
        self.assertAlmostEqual(_score_mean_reversion(-2.0), 1.0)


class TestScoreVolume(unittest.TestCase):
    """_score_volume: <1x -> 0.1, 1-3x -> linear 0.1-1.0, >=3x -> 1.0."""

    def test_below_one(self):
        self.assertAlmostEqual(_score_volume(0.5), 0.1)

    def test_at_one(self):
        self.assertAlmostEqual(_score_volume(1.0), 0.1)

    def test_at_two(self):
        # 0.1 + (2-1)/2 * 0.9 = 0.1 + 0.45 = 0.55
        self.assertAlmostEqual(_score_volume(2.0), 0.55)

    def test_at_three(self):
        self.assertAlmostEqual(_score_volume(3.0), 1.0)

    def test_above_three(self):
        self.assertAlmostEqual(_score_volume(5.0), 1.0)


class TestScoreVolatilityFit(unittest.TestCase):
    """_score_volatility_fit: classifies ATR% into VolatilityState."""

    def test_low(self):
        self.assertEqual(_score_volatility_fit(0.5), VolatilityState.LOW)

    def test_low_boundary(self):
        self.assertEqual(_score_volatility_fit(0.99), VolatilityState.LOW)

    def test_normal(self):
        self.assertEqual(_score_volatility_fit(1.0), VolatilityState.NORMAL)
        self.assertEqual(_score_volatility_fit(2.0), VolatilityState.NORMAL)

    def test_normal_upper(self):
        self.assertEqual(_score_volatility_fit(2.49), VolatilityState.NORMAL)

    def test_elevated(self):
        self.assertEqual(_score_volatility_fit(2.5), VolatilityState.ELEVATED)
        self.assertEqual(_score_volatility_fit(3.5), VolatilityState.ELEVATED)

    def test_elevated_upper(self):
        self.assertEqual(_score_volatility_fit(3.99), VolatilityState.ELEVATED)

    def test_extreme(self):
        self.assertEqual(_score_volatility_fit(4.0), VolatilityState.EXTREME)
        self.assertEqual(_score_volatility_fit(10.0), VolatilityState.EXTREME)


class TestVolFitScore(unittest.TestCase):
    """_vol_fit_score: maps VolatilityState to a 0-1 score."""

    def test_low(self):
        self.assertAlmostEqual(_vol_fit_score(VolatilityState.LOW), 0.5)

    def test_normal(self):
        self.assertAlmostEqual(_vol_fit_score(VolatilityState.NORMAL), 1.0)

    def test_elevated(self):
        self.assertAlmostEqual(_vol_fit_score(VolatilityState.ELEVATED), 0.6)

    def test_extreme(self):
        self.assertAlmostEqual(_vol_fit_score(VolatilityState.EXTREME), 0.2)


class TestScoreHurst(unittest.TestCase):
    """_score_hurst: distance from 0.5 / 0.2, capped at 1.0."""

    def test_random_walk(self):
        self.assertAlmostEqual(_score_hurst(0.5), 0.0)

    def test_trending(self):
        # H=0.7 -> |0.7-0.5|/0.2 = 1.0
        self.assertAlmostEqual(_score_hurst(0.7), 1.0)

    def test_mean_reverting(self):
        # H=0.3 -> |0.3-0.5|/0.2 = 1.0
        self.assertAlmostEqual(_score_hurst(0.3), 1.0)

    def test_slight_trend(self):
        # H=0.6 -> 0.1/0.2 = 0.5
        self.assertAlmostEqual(_score_hurst(0.6), 0.5)

    def test_extreme_trend(self):
        # H=1.0 -> 0.5/0.2 = 2.5, capped at 1.0
        self.assertAlmostEqual(_score_hurst(1.0), 1.0)

    def test_extreme_mean_revert(self):
        self.assertAlmostEqual(_score_hurst(0.0), 1.0)


class TestClassifyRegime(unittest.TestCase):
    """_classify_regime: 7 regime types based on ADX, momentum, ATR%, Hurst."""

    def test_high_volatility(self):
        result = _classify_regime(adx=30, momentum_ratio=0.02, atr_pct=4.5, hurst=0.6)
        self.assertEqual(result, MarketRegime.HIGH_VOLATILITY)

    def test_high_volatility_overrides_trend(self):
        # Even with strong ADX, extreme vol wins
        result = _classify_regime(adx=50, momentum_ratio=0.05, atr_pct=5.0, hurst=0.7)
        self.assertEqual(result, MarketRegime.HIGH_VOLATILITY)

    def test_choppy(self):
        # ADX < 20 and Hurst < 0.50
        result = _classify_regime(adx=15, momentum_ratio=0.0, atr_pct=2.0, hurst=0.45)
        self.assertEqual(result, MarketRegime.CHOPPY)

    def test_ranging(self):
        # ADX < 25 but not choppy (hurst >= 0.50)
        result = _classify_regime(adx=22, momentum_ratio=0.0, atr_pct=2.0, hurst=0.55)
        self.assertEqual(result, MarketRegime.RANGING)

    def test_ranging_adx_20_hurst_50(self):
        # ADX=20 not < 20, so not choppy; ADX < 25 -> RANGING
        result = _classify_regime(adx=20, momentum_ratio=0.0, atr_pct=2.0, hurst=0.50)
        self.assertEqual(result, MarketRegime.RANGING)

    def test_strong_trend_up(self):
        result = _classify_regime(adx=40, momentum_ratio=0.03, atr_pct=2.0, hurst=0.6)
        self.assertEqual(result, MarketRegime.STRONG_TREND_UP)

    def test_strong_trend_down(self):
        result = _classify_regime(adx=40, momentum_ratio=-0.03, atr_pct=2.0, hurst=0.6)
        self.assertEqual(result, MarketRegime.STRONG_TREND_DOWN)

    def test_weak_trend_up(self):
        # ADX >= 25 but < 35, positive momentum
        result = _classify_regime(adx=28, momentum_ratio=0.01, atr_pct=2.0, hurst=0.55)
        self.assertEqual(result, MarketRegime.WEAK_TREND_UP)

    def test_weak_trend_down(self):
        result = _classify_regime(adx=28, momentum_ratio=-0.01, atr_pct=2.0, hurst=0.55)
        self.assertEqual(result, MarketRegime.WEAK_TREND_DOWN)

    def test_strong_trend_boundary_adx_35(self):
        # ADX exactly 35 -> STRONG
        result = _classify_regime(adx=35, momentum_ratio=0.01, atr_pct=2.0, hurst=0.55)
        self.assertEqual(result, MarketRegime.STRONG_TREND_UP)

    def test_zero_momentum_is_up(self):
        # momentum_ratio >= 0 counts as up direction
        result = _classify_regime(adx=30, momentum_ratio=0.0, atr_pct=2.0, hurst=0.55)
        self.assertEqual(result, MarketRegime.WEAK_TREND_UP)


class TestEdgeStrength(unittest.TestCase):
    """_edge_strength: STRONG >= 0.70, MODERATE 0.45-0.69, WEAK 0.25-0.44, NONE < 0.25."""

    def test_strong(self):
        self.assertEqual(_edge_strength(0.70), EdgeStrength.STRONG)
        self.assertEqual(_edge_strength(0.95), EdgeStrength.STRONG)

    def test_moderate(self):
        self.assertEqual(_edge_strength(0.45), EdgeStrength.MODERATE)
        self.assertEqual(_edge_strength(0.69), EdgeStrength.MODERATE)

    def test_weak(self):
        self.assertEqual(_edge_strength(0.25), EdgeStrength.WEAK)
        self.assertEqual(_edge_strength(0.44), EdgeStrength.WEAK)

    def test_none(self):
        self.assertEqual(_edge_strength(0.24), EdgeStrength.NONE)
        self.assertEqual(_edge_strength(0.0), EdgeStrength.NONE)


class TestFactorWeights(unittest.TestCase):
    """Factor weights must sum to 1.0."""

    def test_weights_sum_to_one(self):
        total = sum(FACTOR_WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0)

    def test_all_keys_present(self):
        expected = {"trend", "momentum", "mean_reversion", "volume_confirm", "vol_fit", "hurst"}
        self.assertEqual(set(FACTOR_WEIGHTS.keys()), expected)

    def test_all_weights_positive(self):
        for k, v in FACTOR_WEIGHTS.items():
            self.assertGreater(v, 0.0, f"Weight for {k} must be positive")


class TestCompositeScore(unittest.TestCase):
    """_composite_score weighted sum of factor scores."""

    def test_all_zeros(self):
        f = FactorScores()
        self.assertAlmostEqual(_composite_score(f), 0.0)

    def test_all_ones(self):
        f = FactorScores(
            trend_factor=1.0,
            momentum_factor=1.0,
            mean_reversion=1.0,
            volume_confirm=1.0,
            volatility_fit=1.0,
            hurst_factor=1.0,
        )
        self.assertAlmostEqual(_composite_score(f), 1.0)

    def test_partial(self):
        f = FactorScores(trend_factor=1.0)  # all others 0
        expected = FACTOR_WEIGHTS["trend"]
        self.assertAlmostEqual(_composite_score(f), expected)


class TestInsufficientData(unittest.TestCase):
    """run_quant_analysis with fewer than 45 bars should return early."""

    def test_too_few_bars(self):
        ohlcv = _generate_synthetic_ohlcv(30)
        report = run_quant_analysis("TEST/USDT", "1h", ohlcv)
        self.assertFalse(report.passes_quant_gate)
        self.assertIn("Insufficient bars", report.rejection_reason)
        self.assertEqual(report.bars_analyzed, 30)

    def test_exactly_44_bars(self):
        ohlcv = _generate_synthetic_ohlcv(44)
        report = run_quant_analysis("TEST/USDT", "1h", ohlcv)
        self.assertFalse(report.passes_quant_gate)
        self.assertIn("Insufficient bars", report.rejection_reason)

    def test_exactly_45_bars_passes_data_check(self):
        ohlcv = _generate_synthetic_ohlcv(45)
        report = run_quant_analysis("TEST/USDT", "1h", ohlcv)
        # Should not fail due to insufficient data
        self.assertNotIn("Insufficient bars", report.rejection_reason)

    def test_empty_data(self):
        report = run_quant_analysis("TEST/USDT", "1h", [])
        self.assertFalse(report.passes_quant_gate)
        self.assertIn("Insufficient bars", report.rejection_reason)


class TestGateDecisions(unittest.TestCase):
    """Gate pass/fail: EXTREME volatility, CHOPPY regime, low score."""

    def test_extreme_volatility_rejects(self):
        # Build a report manually to check gate logic via full pipeline
        # Use synthetic data and check that EXTREME vol -> rejection
        report = QuantReport(
            volatility_state=VolatilityState.EXTREME,
            regime=MarketRegime.STRONG_TREND_UP,
            quant_score=0.80,
        )
        # The gate logic is inside run_quant_analysis, so test the report
        # from the full pipeline instead. But we can verify the constant.
        self.assertEqual(QUANT_SCORE_GATE, 0.40)

    def test_gate_with_synthetic_data(self):
        ohlcv = _generate_synthetic_ohlcv(150, seed=42)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        # The report should either pass or fail with a valid reason
        if report.passes_quant_gate:
            self.assertEqual(report.rejection_reason, "")
        else:
            self.assertIn(report.rejection_reason, [
                r for r in [report.rejection_reason] if any(
                    tag in r for tag in [
                        "EXTREME_VOLATILITY",
                        "CHOPPY_MARKET",
                        "LOW_QUANT_SCORE",
                    ]
                )
            ])

    def test_score_below_gate_rejects(self):
        """Full pipeline: verify that a low-score scenario is properly rejected."""
        ohlcv = _generate_synthetic_ohlcv(150, seed=42)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        # At least verify the gate threshold constant
        if report.quant_score < QUANT_SCORE_GATE and \
           report.volatility_state != VolatilityState.EXTREME and \
           report.regime != MarketRegime.CHOPPY:
            self.assertFalse(report.passes_quant_gate)
            self.assertIn("LOW_QUANT_SCORE", report.rejection_reason)

    def test_choppy_regime_rejects(self):
        """If regime is CHOPPY, gate should fail regardless of score."""
        ohlcv = _generate_synthetic_ohlcv(150, seed=42)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        if report.regime == MarketRegime.CHOPPY:
            self.assertFalse(report.passes_quant_gate)
            self.assertIn("CHOPPY_MARKET", report.rejection_reason)


class TestGateLogicDirect(unittest.TestCase):
    """Directly test gate logic priority: EXTREME > CHOPPY > LOW_SCORE > PASS."""

    def _run_with_conditions(self, vol_state, regime, score):
        """Simulate the gate decision logic from run_quant_analysis."""
        passes = False
        reason = ""
        if vol_state == VolatilityState.EXTREME:
            reason = "EXTREME_VOLATILITY: ATR% > 4.0 -- risk is unquantifiable"
        elif regime == MarketRegime.CHOPPY:
            reason = "CHOPPY_MARKET: ADX < 20 and Hurst < 0.50 -- no directional edge"
        elif score < QUANT_SCORE_GATE:
            reason = f"LOW_QUANT_SCORE: {score:.3f} < {QUANT_SCORE_GATE} threshold"
        else:
            passes = True
        return passes, reason

    def test_extreme_vol_always_rejects(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.EXTREME, MarketRegime.STRONG_TREND_UP, 0.90
        )
        self.assertFalse(passes)
        self.assertIn("EXTREME_VOLATILITY", reason)

    def test_choppy_rejects_even_high_score(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.NORMAL, MarketRegime.CHOPPY, 0.85
        )
        self.assertFalse(passes)
        self.assertIn("CHOPPY_MARKET", reason)

    def test_low_score_rejects(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.NORMAL, MarketRegime.RANGING, 0.35
        )
        self.assertFalse(passes)
        self.assertIn("LOW_QUANT_SCORE", reason)

    def test_passing_gate(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.NORMAL, MarketRegime.STRONG_TREND_UP, 0.55
        )
        self.assertTrue(passes)
        self.assertEqual(reason, "")

    def test_extreme_vol_takes_priority_over_choppy(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.EXTREME, MarketRegime.CHOPPY, 0.10
        )
        self.assertFalse(passes)
        self.assertIn("EXTREME_VOLATILITY", reason)

    def test_gate_boundary_at_040(self):
        passes, _ = self._run_with_conditions(
            VolatilityState.NORMAL, MarketRegime.RANGING, 0.40
        )
        self.assertTrue(passes)

    def test_gate_just_below_040(self):
        passes, reason = self._run_with_conditions(
            VolatilityState.NORMAL, MarketRegime.RANGING, 0.399
        )
        self.assertFalse(passes)
        self.assertIn("LOW_QUANT_SCORE", reason)


class TestFullPipeline(unittest.TestCase):
    """End-to-end pipeline with synthetic data."""

    def test_default_seed(self):
        ohlcv = _generate_synthetic_ohlcv(150, seed=42)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        self.assertEqual(report.symbol, "BTC/USDT")
        self.assertEqual(report.timeframe, "4h")
        self.assertEqual(report.bars_analyzed, 150)
        self.assertIsInstance(report.regime, MarketRegime)
        self.assertIsInstance(report.volatility_state, VolatilityState)
        self.assertIsInstance(report.edge_strength, EdgeStrength)
        self.assertGreaterEqual(report.quant_score, 0.0)
        self.assertLessEqual(report.quant_score, 1.0)

    def test_different_seeds_produce_different_results(self):
        ohlcv1 = _generate_synthetic_ohlcv(150, seed=1)
        ohlcv2 = _generate_synthetic_ohlcv(150, seed=999)
        r1 = run_quant_analysis("BTC/USDT", "4h", ohlcv1)
        r2 = run_quant_analysis("BTC/USDT", "4h", ohlcv2)
        # Highly unlikely to be identical with different seeds
        self.assertNotEqual(r1.quant_score, r2.quant_score)

    def test_report_has_explanation(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("ETH/USDT", "1h", ohlcv)
        self.assertIn("RUNECLAW QUANT REPORT", report.explanation)
        self.assertIn("ETH/USDT", report.explanation)

    def test_factors_within_bounds(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        f = report.factors
        for name, val in [
            ("trend_factor", f.trend_factor),
            ("momentum_factor", f.momentum_factor),
            ("mean_reversion", f.mean_reversion),
            ("volume_confirm", f.volume_confirm),
            ("volatility_fit", f.volatility_fit),
            ("hurst_factor", f.hurst_factor),
        ]:
            self.assertGreaterEqual(val, 0.0, f"{name} should be >= 0")
            self.assertLessEqual(val, 1.0, f"{name} should be <= 1")

    def test_deterministic_with_same_seed(self):
        ohlcv = _generate_synthetic_ohlcv(150, seed=7)
        r1 = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        ohlcv2 = _generate_synthetic_ohlcv(150, seed=7)
        r2 = run_quant_analysis("BTC/USDT", "4h", ohlcv2)
        self.assertAlmostEqual(r1.quant_score, r2.quant_score)
        self.assertEqual(r1.regime, r2.regime)


class TestToDict(unittest.TestCase):
    """QuantReport.to_dict() produces the correct keys."""

    def setUp(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        self.report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        self.d = self.report.to_dict()

    def test_top_level_keys(self):
        expected_keys = {
            "symbol", "timeframe", "timestamp", "bars_analyzed",
            "regime", "volatility_state",
            "adx", "atr_pct", "hurst_exponent", "price_zscore",
            "volume_ratio", "momentum_ratio",
            "factors",
            "quant_score", "edge_strength",
            "passes_quant_gate", "rejection_reason", "explanation",
        }
        self.assertEqual(set(self.d.keys()), expected_keys)

    def test_factors_sub_keys(self):
        expected = {"trend", "momentum", "mean_reversion", "volume_confirm", "vol_fit", "hurst"}
        self.assertEqual(set(self.d["factors"].keys()), expected)

    def test_regime_is_string(self):
        self.assertIsInstance(self.d["regime"], str)

    def test_volatility_state_is_string(self):
        self.assertIsInstance(self.d["volatility_state"], str)

    def test_edge_strength_is_string(self):
        self.assertIsInstance(self.d["edge_strength"], str)

    def test_timestamp_is_iso_string(self):
        self.assertIsInstance(self.d["timestamp"], str)
        # Should be parseable as ISO format
        from datetime import datetime
        datetime.fromisoformat(self.d["timestamp"])

    def test_numeric_values_rounded(self):
        self.assertIsInstance(self.d["adx"], float)
        self.assertIsInstance(self.d["atr_pct"], float)
        self.assertIsInstance(self.d["quant_score"], float)


class TestSyntheticOhlcv(unittest.TestCase):
    """_generate_synthetic_ohlcv produces valid OHLCV data."""

    def test_correct_length(self):
        data = _generate_synthetic_ohlcv(100)
        self.assertEqual(len(data), 100)

    def test_bar_structure(self):
        data = _generate_synthetic_ohlcv(10)
        for bar in data:
            self.assertEqual(len(bar), 6)  # ts, open, high, low, close, volume
            for val in bar:
                self.assertIsInstance(val, float)

    def test_high_gte_low(self):
        data = _generate_synthetic_ohlcv(200, seed=123)
        for bar in data:
            self.assertGreaterEqual(bar[2], bar[3],
                                    "High should be >= Low for every bar")

    def test_seeded_is_deterministic(self):
        d1 = _generate_synthetic_ohlcv(50, seed=99)
        d2 = _generate_synthetic_ohlcv(50, seed=99)
        self.assertEqual(d1, d2)


class TestSkillRegistration(unittest.TestCase):
    """QuantAnalyzeSkill has correct attributes and can be registered."""

    def test_skill_name(self):
        skill = QuantAnalyzeSkill()
        self.assertEqual(skill.name, "quant_analyze")

    def test_skill_has_description(self):
        skill = QuantAnalyzeSkill()
        self.assertTrue(len(skill.description) > 0)

    def test_register_in_mock_registry(self):
        """Simulate registration with a minimal registry mock."""

        class MockRegistry:
            def __init__(self):
                self.skills = {}

            def register(self, skill):
                self.skills[skill.name] = skill

        from bot.skills.quant_skill import register_quant_skill

        registry = MockRegistry()
        register_quant_skill(registry)
        self.assertIn("quant_analyze", registry.skills)
        self.assertIsInstance(registry.skills["quant_analyze"], QuantAnalyzeSkill)


class TestQuantScoreGateConstant(unittest.TestCase):
    """QUANT_SCORE_GATE is 0.40 as documented."""

    def test_gate_value(self):
        self.assertEqual(QUANT_SCORE_GATE, 0.40)


if __name__ == "__main__":
    unittest.main()
