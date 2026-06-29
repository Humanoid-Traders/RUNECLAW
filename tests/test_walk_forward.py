"""
Walk-forward analysis harness.

The fold-splitting + aggregation + overfit logic is tested deterministically with
an injected stub backtest function (no heavy engine, no network). Covers fold
geometry, rolling robustness, anchored IS->OOS optimisation + the overfitting
gap, and the robustness classification.
"""

import pytest

from bot.backtest.walk_forward import make_folds, run_walk_forward, Fold


class _Result:
    def __init__(self, total_return_pct=0.0, win_rate=0.5, total_trades=10,
                 sharpe_ratio=1.0, max_drawdown_pct=5.0):
        self.total_return_pct = total_return_pct
        self.win_rate = win_rate
        self.total_trades = total_trades
        self.sharpe_ratio = sharpe_ratio
        self.max_drawdown_pct = max_drawdown_pct


def test_make_folds_geometry():
    folds = make_folds(1000, n_folds=4, is_min_frac=0.4, min_oos_bars=20)
    assert len(folds) == 4
    # Anchored: every fold's IS starts at 0 and ends where its OOS starts.
    for f in folds:
        assert f.is_start == 0
        assert f.is_end == f.oos_start
        assert f.oos_end > f.oos_start
    # OOS blocks are contiguous and cover to the end.
    assert folds[0].oos_start == 400
    assert folds[-1].oos_end == 1000
    for a, b in zip(folds, folds[1:]):
        assert a.oos_end == b.oos_start


def test_make_folds_rejects_insufficient_data():
    with pytest.raises(ValueError):
        make_folds(100, n_folds=10, is_min_frac=0.4, min_oos_bars=20)
    with pytest.raises(ValueError):
        make_folds(1000, n_folds=0)


@pytest.mark.asyncio
async def test_rolling_robustness_no_grid():
    bars = list(range(1000))
    # Stub: OOS return = +2% always (profitable, consistent).
    async def bt(slice_bars, overrides):
        return _Result(total_return_pct=2.0)
    report = await run_walk_forward(bars, {}, n_folds=4, backtest_fn=bt)
    assert len(report.folds) == 4
    assert report.pct_profitable_folds == 1.0
    assert report.mean_oos_return == pytest.approx(2.0)
    assert report.std_oos_return == pytest.approx(0.0)
    assert report.overfitting_gap is None        # no grid -> no IS objective
    assert "ROBUST" in report.robustness


@pytest.mark.asyncio
async def test_fragile_when_mostly_unprofitable():
    bars = list(range(1000))
    calls = {"n": 0}
    async def bt(slice_bars, overrides):
        calls["n"] += 1
        # Only the first fold profits; the rest lose.
        return _Result(total_return_pct=5.0 if calls["n"] == 1 else -1.0)
    report = await run_walk_forward(bars, {}, n_folds=4, backtest_fn=bt)
    assert report.pct_profitable_folds == 0.25
    assert "FRAGILE" in report.robustness


@pytest.mark.asyncio
async def test_optimization_picks_best_is_and_reports_gap():
    bars = list(range(1200))
    # Grid of thresholds; IS prefers 0.45 (highest IS return) but it overfits:
    # out-of-sample it does worse. Encode that via the override value.
    grid = [{"confidence_threshold": t} for t in (0.45, 0.6)]

    async def bt(slice_bars, overrides):
        thr = overrides["confidence_threshold"]
        # In-sample slices are long (anchored, >= warmup); OOS slices are short.
        is_sample = len(slice_bars) > 300
        if thr == 0.45:
            return _Result(total_return_pct=20.0 if is_sample else 1.0)  # overfit
        return _Result(total_return_pct=8.0 if is_sample else 6.0)       # robust

    report = await run_walk_forward(bars, {}, n_folds=3, param_grid=grid, backtest_fn=bt)
    # IS optimiser always prefers 0.45 (20 > 8 in-sample).
    assert all(f.chosen["confidence_threshold"] == 0.45 for f in report.folds)
    # Overfitting gap = mean IS (20) - mean OOS (1) = +19, large -> OVERFIT.
    assert report.overfitting_gap == pytest.approx(19.0)
    assert "OVERFIT" in report.robustness


@pytest.mark.asyncio
async def test_fold_metrics_populated():
    bars = list(range(600))
    async def bt(slice_bars, overrides):
        return _Result(total_return_pct=3.0, win_rate=0.6, total_trades=12,
                       sharpe_ratio=1.5, max_drawdown_pct=4.0)
    report = await run_walk_forward(bars, {}, n_folds=3, min_oos_bars=20, backtest_fn=bt)
    for f in report.folds:
        assert isinstance(f, Fold)
        assert f.oos_win_rate == 0.6 and f.oos_trades == 12
        assert f.oos_sharpe == 1.5 and f.oos_max_dd == 4.0
    assert "walk-forward:" in report.summary()
