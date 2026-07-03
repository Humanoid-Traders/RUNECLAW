"""Backtest smoke: a seeded synthetic run completes end-to-end.

Catches pipeline-killing regressions (unhandled exceptions mid-run, latched
gates that raise, broken result assembly) at PR time instead of during a
multi-hour measurement suite. This is a LIVENESS check, not a performance
benchmark — synthetic data carries no edge, so no return/PF assertions.
"""

import asyncio

from bot.backtest.data_loader import DataLoader
from bot.backtest.engine import BacktestEngine
from bot.backtest.models import BacktestConfig


class TestBacktestSmoke:
    def test_synthetic_run_completes(self):
        bars = DataLoader.generate_synthetic(bars=400, seed=42)
        eng = BacktestEngine(BacktestConfig(
            symbol="BTC/USDT", timeframe="1h", initial_balance=10_000.0,
            fill_mode="next_open"))
        try:
            result = asyncio.run(eng.run(bars))
        finally:
            eng.cleanup()
        assert result is not None
        assert result.total_trades >= 0
        assert result.final_equity > 0
        # The engine must have actually analyzed bars, not short-circuited:
        # generated + both rejection buckets can't ALL be zero on 400 bars.
        activity = (result.total_ideas_generated
                    + result.total_ideas_rejected_confidence
                    + result.total_ideas_rejected_risk)
        assert activity > 0

    def test_synthetic_run_deterministic(self):
        def _once():
            eng = BacktestEngine(BacktestConfig(
                symbol="BTC/USDT", timeframe="1h", initial_balance=10_000.0,
                fill_mode="next_open"))
            try:
                return asyncio.run(eng.run(
                    DataLoader.generate_synthetic(bars=300, seed=7)))
            finally:
                eng.cleanup()
        a, b = _once(), _once()
        assert (a.total_trades, round(a.final_equity, 6)) == \
               (b.total_trades, round(b.final_equity, 6))
