"""PortfolioBacktester: multi-symbol backtest over ONE shared portfolio/risk
state — the measurement the live system actually corresponds to."""
from __future__ import annotations

import pytest

from bot.backtest.data_loader import DataLoader
from bot.backtest.models import BacktestConfig
from bot.backtest.portfolio_engine import PortfolioBacktester, portfolio_walk_forward
from bot.config import CONFIG


def _data(n=600):
    return {
        "BTC/USDT": DataLoader.generate_synthetic(bars=n, seed=3),
        "ETH/USDT": DataLoader.generate_synthetic(bars=n, seed=6, start_price=3000.0),
    }


class TestPortfolioBacktester:
    @pytest.mark.asyncio
    async def test_shared_state_and_aggregate_result(self):
        data = _data()
        pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0),
                                 symbols=list(data))
        # All sub-engines must share the SAME portfolio/risk/analyzer objects.
        engines = list(pb._engines.values())
        assert all(e.portfolio is engines[0].portfolio for e in engines)
        assert all(e.risk is engines[0].risk for e in engines)
        assert all(e.analyzer is engines[0].analyzer for e in engines)

        res = await pb.run(data)
        pb.cleanup()
        assert res.symbol == "BTC/USDT+ETH/USDT"
        # Aggregate trade count equals the per-symbol sum.
        assert res.total_trades == sum(r["trades"] for r in pb.per_symbol.values())
        assert set(pb.per_symbol) == set(data)
        # Equity curve is system-level and non-empty.
        assert res.final_equity > 0

    @pytest.mark.asyncio
    async def test_learning_flags_restored_after_cleanup(self):
        before = (CONFIG.analyzer.confidence_calibration_enabled,
                  CONFIG.analyzer.setup_expectancy_enabled)
        data = _data(n=300)
        pb = PortfolioBacktester(BacktestConfig(), symbols=list(data))
        # Forced OFF during the run (audit fix #25 semantics preserved).
        assert CONFIG.analyzer.confidence_calibration_enabled is False
        await pb.run(data)
        pb.cleanup()
        after = (CONFIG.analyzer.confidence_calibration_enabled,
                 CONFIG.analyzer.setup_expectancy_enabled)
        assert after == before

    @pytest.mark.asyncio
    async def test_deterministic_across_runs(self):
        data = _data(n=500)
        results = []
        for _ in range(2):
            pb = PortfolioBacktester(BacktestConfig(initial_balance=10_000.0),
                                     symbols=list(data))
            res = await pb.run(data)
            pb.cleanup()
            results.append((res.total_trades, round(res.total_return_pct, 4)))
        assert results[0] == results[1]

    def test_requires_symbols(self):
        with pytest.raises(ValueError):
            PortfolioBacktester(BacktestConfig(), symbols=[])


class TestPortfolioWalkForward:
    @pytest.mark.asyncio
    async def test_folds_produced_with_summary_fields(self):
        data = _data(n=900)
        folds = await portfolio_walk_forward(data, BacktestConfig(), n_folds=3)
        assert len(folds) >= 1
        for f in folds:
            assert {"fold", "trades", "return_pct", "win_rate",
                    "max_dd_pct", "per_symbol"} <= set(f)
