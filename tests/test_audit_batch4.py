"""Audit top-25 fixes, batch 4 (low).

#22 VWAP band center/dispersion computed against the same anchor
#25 Walk-forward embargo + calibration isolation in backtest
"""
from __future__ import annotations

import numpy as np
import pytest

from bot.config import CONFIG
from bot.core.analyzer import Analyzer


class TestVwapBandAnchor:
    def test_bands_symmetric_around_vwap(self):
        n = 60
        closes = 100.0 + np.cumsum(np.sin(np.arange(n)))
        highs, lows = closes + 1.0, closes - 1.0
        vols = np.full(n, 500.0)
        times = np.arange(n, dtype=float) * 3600_000
        ind = Analyzer._compute_indicators(highs, lows, closes, vols, times=times)
        assert ind is not None
        center = ind["vwap"]
        # Bands must be exactly symmetric around the SAME vwap value consumers
        # read (audit fix #22 — previously dispersion used a different series).
        assert ind["vwap_upper_1"] - center == pytest.approx(center - ind["vwap_lower_1"], abs=1e-4)
        assert ind["vwap_upper_2"] - center == pytest.approx(
            2 * (ind["vwap_upper_1"] - center), abs=1e-4)


class TestBacktestCalibrationIsolation:
    @pytest.mark.asyncio
    async def test_calibration_flags_forced_off_and_restored(self):
        from bot.backtest.engine import BacktestEngine
        from bot.backtest.models import BacktestConfig

        before = (CONFIG.analyzer.confidence_calibration_enabled,
                  CONFIG.analyzer.setup_expectancy_enabled)
        eng = BacktestEngine(BacktestConfig(symbol="BTC/USDT"))
        # During the backtest the full-history learners must be OFF (they are
        # fitted on future data relative to replayed bars — audit fix #25).
        assert CONFIG.analyzer.confidence_calibration_enabled is False
        assert CONFIG.analyzer.setup_expectancy_enabled is False
        eng.cleanup()
        after = (CONFIG.analyzer.confidence_calibration_enabled,
                 CONFIG.analyzer.setup_expectancy_enabled)
        assert after == before


class TestWalkForwardEmbargo:
    @pytest.mark.asyncio
    async def test_embargo_shrinks_in_sample(self):
        from bot.backtest.walk_forward import run_walk_forward

        seen_lengths: list[tuple[int, int]] = []

        async def fake_backtest(bars, overrides):
            class R:
                total_return_pct = 1.0
                win_rate = 0.5
                total_trades = 1
                sharpe_ratio = 0.1
                max_drawdown_pct = 1.0
            seen_lengths.append((len(bars), 0))
            return R()

        bars = list(range(400))
        await run_walk_forward(
            bars, {}, n_folds=2, embargo_bars=12,
            param_grid=[{"confidence_threshold": 0.5}],
            backtest_fn=fake_backtest)
        # First call per fold is the IS run; with the embargo the IS slice must
        # end 12 bars before the OOS start (fold0 IS would be 160 bars without
        # the embargo, 148 with it).
        assert any(n == 148 for n, _ in seen_lengths)
