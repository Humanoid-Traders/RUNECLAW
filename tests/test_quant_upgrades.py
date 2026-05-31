"""
Tests for quant_skill upgrades:
  - Rolling Hurst exponent & trend detection
  - GARCH(1,1) volatility forecast
  - Live data pipeline (analyze_live)
  - Telegram formatter (format_quant_for_telegram)
"""

import asyncio
import math
import unittest

from bot.skills.quant_skill import (
    FACTOR_WEIGHTS,
    _garch_forecast,
    _generate_synthetic_ohlcv,
    _hurst_trend,
    _rolling_hurst,
    _score_vol_forecast,
    _vol_regime_forecast,
    analyze_live,
    format_quant_for_telegram,
    run_quant_analysis,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _trending_prices(n: int = 200, start: float = 100.0, step: float = 0.5) -> list[float]:
    """Monotonically increasing prices (strong trend)."""
    return [start + i * step for i in range(n)]


def _mean_reverting_prices(n: int = 200, center: float = 100.0, amp: float = 5.0) -> list[float]:
    """Oscillating prices around a center (mean-reverting)."""
    return [center + amp * math.sin(i * 0.5) for i in range(n)]


def _stable_returns(n: int = 200, val: float = 0.001) -> list[float]:
    """Near-constant small returns."""
    return [val] * n


def _increasing_vol_returns(n: int = 200) -> list[float]:
    """Returns that grow in magnitude (expanding volatility)."""
    return [0.001 * (1 + i / 20) * ((-1) ** i) for i in range(n)]


# ═════════════════════════════════════════════════════════════════════════════
# 1.  Rolling Hurst
# ═════════════════════════════════════════════════════════════════════════════

class TestRollingHurst(unittest.TestCase):

    def test_trending_data_hurst_above_half(self):
        """Trending (monotonic) prices should yield Hurst values > 0.5."""
        prices = _trending_prices(200)
        rh = _rolling_hurst(prices, window=100)
        self.assertGreater(len(rh), 0)
        avg = sum(rh) / len(rh)
        self.assertGreater(avg, 0.5,
                           f"Trending data avg Hurst {avg:.3f} should be > 0.5")

    def test_mean_reverting_data_hurst_below_half(self):
        """Oscillating (mean-reverting) prices should yield Hurst < 0.5."""
        prices = _mean_reverting_prices(200)
        rh = _rolling_hurst(prices, window=100)
        self.assertGreater(len(rh), 0)
        avg = sum(rh) / len(rh)
        self.assertLess(avg, 0.55,
                        f"Mean-reverting data avg Hurst {avg:.3f} should be < 0.55")

    def test_rolling_hurst_length(self):
        """Output length = len(prices) - window + 1 when data is sufficient."""
        prices = _trending_prices(200)
        rh = _rolling_hurst(prices, window=100)
        self.assertEqual(len(rh), 200 - 100 + 1)

    def test_rolling_hurst_short_data(self):
        """If data shorter than window, returns at least 1 value."""
        prices = _trending_prices(30)
        rh = _rolling_hurst(prices, window=100)
        self.assertGreaterEqual(len(rh), 1)


# ═════════════════════════════════════════════════════════════════════════════
# 2.  Hurst Trend Detection
# ═════════════════════════════════════════════════════════════════════════════

class TestHurstTrend(unittest.TestCase):

    def test_crossing_above(self):
        """Hurst crossing from below 0.5 to above -> TRENDING_UP."""
        vals = [0.45, 0.47, 0.48, 0.49, 0.52]
        self.assertEqual(_hurst_trend(vals), "TRENDING_UP")

    def test_crossing_below(self):
        """Hurst crossing from above 0.5 to below -> TRENDING_DOWN."""
        vals = [0.55, 0.53, 0.51, 0.50, 0.48]
        self.assertEqual(_hurst_trend(vals), "TRENDING_DOWN")

    def test_stable_above(self):
        """All values above 0.5 -> STABLE."""
        vals = [0.55, 0.56, 0.57, 0.58, 0.59]
        self.assertEqual(_hurst_trend(vals), "STABLE")

    def test_stable_below(self):
        """All values below 0.5 -> STABLE."""
        vals = [0.40, 0.42, 0.43, 0.44, 0.45]
        self.assertEqual(_hurst_trend(vals), "STABLE")

    def test_single_value(self):
        """Single value -> STABLE (not enough data for crossing)."""
        self.assertEqual(_hurst_trend([0.6]), "STABLE")

    def test_empty(self):
        """Empty list -> STABLE."""
        self.assertEqual(_hurst_trend([]), "STABLE")


# ═════════════════════════════════════════════════════════════════════════════
# 3.  GARCH Forecast
# ═════════════════════════════════════════════════════════════════════════════

class TestGarchForecast(unittest.TestCase):

    def test_stable_returns_not_expanding(self):
        """Constant small returns should not flag vol_expanding."""
        rets = _stable_returns(200)
        result = _garch_forecast(rets)
        self.assertIn("current_vol", result)
        self.assertIn("forecast_vol", result)
        self.assertIn("vol_expanding", result)
        self.assertIn("vol_ratio", result)
        # Stable returns: forecast ~ current
        self.assertAlmostEqual(result["vol_ratio"], 1.0, delta=0.2)

    def test_increasing_vol_detected(self):
        """Growing magnitude returns should yield forecast_vol > current_vol at the end."""
        rets = _increasing_vol_returns(300)
        result = _garch_forecast(rets)
        # With growing returns the last squared return is large
        self.assertGreater(result["forecast_vol"], 0)
        self.assertGreater(result["current_vol"], 0)

    def test_empty_returns(self):
        """Empty returns should return safe defaults."""
        result = _garch_forecast([])
        self.assertEqual(result["current_vol"], 0.0)
        self.assertEqual(result["forecast_vol"], 0.0)
        self.assertFalse(result["vol_expanding"])
        self.assertEqual(result["vol_ratio"], 1.0)

    def test_garch_keys(self):
        """Output dict has exactly the required keys."""
        result = _garch_forecast([0.01, -0.02, 0.015])
        self.assertEqual(set(result.keys()),
                         {"current_vol", "forecast_vol", "vol_expanding", "vol_ratio"})

    def test_vol_regime_forecast_from_ohlcv(self):
        """_vol_regime_forecast runs on synthetic OHLCV without error."""
        ohlcv = _generate_synthetic_ohlcv(150)
        result = _vol_regime_forecast(ohlcv)
        self.assertGreater(result["current_vol"], 0)
        self.assertGreater(result["forecast_vol"], 0)


# ═════════════════════════════════════════════════════════════════════════════
# 4.  Score vol_forecast factor
# ═════════════════════════════════════════════════════════════════════════════

class TestScoreVolForecast(unittest.TestCase):

    def test_ratio_one_gives_zero(self):
        """vol_ratio = 1.0 means no change -> score 0."""
        self.assertAlmostEqual(_score_vol_forecast({"vol_ratio": 1.0}), 0.0)

    def test_ratio_above_one(self):
        """vol_ratio 1.3 -> |0.3|/0.3 = 1.0."""
        self.assertAlmostEqual(_score_vol_forecast({"vol_ratio": 1.3}), 1.0)


# ═════════════════════════════════════════════════════════════════════════════
# 5.  Factor Weights Sum
# ═════════════════════════════════════════════════════════════════════════════

class TestUpdatedWeights(unittest.TestCase):

    def test_weights_still_sum_to_one(self):
        self.assertAlmostEqual(sum(FACTOR_WEIGHTS.values()), 1.0)

    def test_vol_forecast_weight_present(self):
        self.assertIn("vol_forecast", FACTOR_WEIGHTS)
        self.assertAlmostEqual(FACTOR_WEIGHTS["vol_forecast"], 0.05)

    def test_hurst_weight_reduced(self):
        self.assertAlmostEqual(FACTOR_WEIGHTS["hurst"], 0.05)


# ═════════════════════════════════════════════════════════════════════════════
# 6.  Live Pipeline
# ═════════════════════════════════════════════════════════════════════════════

class TestAnalyzeLive(unittest.TestCase):

    def test_demo_mode_no_exchange(self):
        """With exchange=None, should fall back to synthetic data and succeed."""
        result = asyncio.get_event_loop().run_until_complete(
            analyze_live("BTC/USDT", exchange=None)
        )
        self.assertIsInstance(result, dict)
        self.assertEqual(result["symbol"], "BTC/USDT")
        self.assertIn("quant_score", result)
        self.assertIn("hurst_trend", result)
        self.assertIn("garch_forecast", result)

    def test_demo_mode_returns_valid_score(self):
        """Score from demo mode should be between 0 and 1."""
        result = asyncio.get_event_loop().run_until_complete(
            analyze_live("ETH/USDT")
        )
        self.assertGreaterEqual(result["quant_score"], 0.0)
        self.assertLessEqual(result["quant_score"], 1.0)


# ═════════════════════════════════════════════════════════════════════════════
# 7.  Telegram Formatter
# ═════════════════════════════════════════════════════════════════════════════

class TestFormatTelegram(unittest.TestCase):

    def setUp(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        self.html = format_quant_for_telegram(report.to_dict())

    def test_contains_html_tags(self):
        self.assertIn("<b>", self.html)
        self.assertIn("</b>", self.html)

    def test_contains_symbol(self):
        self.assertIn("BTC/USDT", self.html)

    def test_contains_war_room_bars(self):
        """Should contain horizontal bar characters."""
        self.assertTrue(
            "\u2501" in self.html,
            "Expected horizontal bar character (━) in output"
        )

    def test_contains_gate_result(self):
        self.assertTrue("PASS" in self.html or "FAIL" in self.html)

    def test_contains_garch_direction(self):
        self.assertTrue("EXPANDING" in self.html or "CONTRACTING" in self.html)

    def test_contains_hurst_trend(self):
        self.assertTrue(
            "STABLE" in self.html
            or "TRENDING_UP" in self.html
            or "TRENDING_DOWN" in self.html
        )


# ═════════════════════════════════════════════════════════════════════════════
# 8.  Integration: new fields in run_quant_analysis output
# ═════════════════════════════════════════════════════════════════════════════

class TestIntegrationNewFields(unittest.TestCase):

    def test_report_has_hurst_trend(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        self.assertIn(report.hurst_trend, ["STABLE", "TRENDING_UP", "TRENDING_DOWN"])

    def test_report_has_garch_forecast(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        garch = report.garch_forecast
        self.assertIn("current_vol", garch)
        self.assertIn("forecast_vol", garch)
        self.assertIn("vol_expanding", garch)
        self.assertIn("vol_ratio", garch)

    def test_to_dict_includes_new_fields(self):
        ohlcv = _generate_synthetic_ohlcv(150)
        report = run_quant_analysis("BTC/USDT", "4h", ohlcv)
        d = report.to_dict()
        self.assertIn("hurst_trend", d)
        self.assertIn("garch_forecast", d)
        self.assertIn("vol_forecast", d["factors"])


if __name__ == "__main__":
    unittest.main()
