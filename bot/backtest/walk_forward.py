"""
Walk-forward analysis — is the strategy robust across time, or curve-fit?

A single backtest over one period is easy to overfit: parameters that look great
on history may just be memorising it. Walk-forward analysis guards against that by
evaluating on **out-of-sample** data the parameters never saw:

  - **Rolling robustness (no param grid):** split the series into sequential
    out-of-sample folds and backtest each independently. Consistent results
    across folds → robust; a few lucky folds carrying the rest → fragile.
  - **Anchored optimisation (with a param grid):** for each fold, pick the best
    parameters on the IN-SAMPLE history, then score them on the next, UNSEEN
    out-of-sample block. The gap between in-sample and out-of-sample performance
    (``overfitting_gap``) is the headline overfit signal.

Offline analysis only — this never trades, never touches live state, and runs the
same deterministic backtester (``use_llm=False``) the rest of the suite uses. The
backtest function is injectable so the fold/aggregation logic is unit-testable
without running the heavy engine.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from statistics import mean, median, pstdev
from typing import Awaitable, Callable, Optional

log = logging.getLogger("runeclaw.walk_forward")

# A backtest function: (bars, config_overrides) -> result with metric attributes.
BacktestFn = Callable[[list, dict], Awaitable[object]]


@dataclass
class Fold:
    index: int
    is_start: int
    is_end: int           # exclusive; also == oos_start
    oos_start: int
    oos_end: int          # exclusive
    chosen: dict = field(default_factory=dict)
    is_objective: Optional[float] = None
    oos_objective: float = 0.0
    oos_return_pct: float = 0.0
    oos_win_rate: float = 0.0
    oos_trades: int = 0
    oos_sharpe: float = 0.0
    oos_max_dd: float = 0.0


@dataclass
class WalkForwardReport:
    folds: list
    pct_profitable_folds: float
    mean_oos_return: float
    median_oos_return: float
    std_oos_return: float
    worst_oos_return: float
    mean_oos_objective: float
    overfitting_gap: Optional[float]   # mean(IS objective) - mean(OOS objective)
    robustness: str

    def summary(self) -> str:
        n = len(self.folds)
        gap = ("n/a" if self.overfitting_gap is None
               else f"{self.overfitting_gap:+.3f}")
        return (f"walk-forward: {n} folds | profitable {self.pct_profitable_folds:.0%} "
                f"| OOS return mean {self.mean_oos_return:+.2f}% median "
                f"{self.median_oos_return:+.2f}% std {self.std_oos_return:.2f} "
                f"| worst {self.worst_oos_return:+.2f}% | overfit gap {gap} "
                f"| {self.robustness}")


def make_folds(n_bars: int, n_folds: int, is_min_frac: float = 0.4,
               min_oos_bars: int = 20) -> list:
    """Anchored (expanding-IS) walk-forward folds.

    The first ``is_min_frac`` of the series is the initial in-sample warmup; the
    remainder is divided into ``n_folds`` sequential out-of-sample blocks. Fold k
    optimises on everything before its OOS block and validates on the block.
    Raises ValueError if there isn't enough data for the requested layout.
    """
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    if not (0.0 < is_min_frac < 1.0):
        raise ValueError("is_min_frac must be in (0, 1)")
    warmup = int(n_bars * is_min_frac)
    oos_total = n_bars - warmup
    if oos_total < n_folds * min_oos_bars:
        raise ValueError(
            f"not enough data: {n_bars} bars, warmup {warmup}, leaves {oos_total} "
            f"for {n_folds} folds (need >= {n_folds * min_oos_bars})")
    block = oos_total // n_folds
    folds = []
    for k in range(n_folds):
        oos_start = warmup + k * block
        oos_end = warmup + (k + 1) * block if k < n_folds - 1 else n_bars
        folds.append(Fold(index=k, is_start=0, is_end=oos_start,
                          oos_start=oos_start, oos_end=oos_end))
    return folds


def _objective(result, key: str = "total_return_pct") -> float:
    """Scalar objective from a backtest result. Defaults to total return %."""
    return float(getattr(result, key, 0.0) or 0.0)


def _classify(report_inputs: dict) -> str:
    pct = report_inputs["pct_profitable_folds"]
    mean_ret = report_inputs["mean_oos_return"]
    gap = report_inputs["overfitting_gap"]
    if mean_ret <= 0 or pct < 0.5:
        return "FRAGILE (out-of-sample edge not established)"
    if gap is not None and gap > max(2.0, abs(mean_ret)):
        return "OVERFIT (large in-sample vs out-of-sample gap)"
    if pct >= 0.7 and mean_ret > 0:
        return "ROBUST (consistent out-of-sample edge)"
    return "MIXED (some out-of-sample edge, watch consistency)"


async def run_walk_forward(
    bars: list,
    base_overrides: Optional[dict] = None,
    *,
    n_folds: int = 4,
    is_min_frac: float = 0.4,
    min_oos_bars: int = 20,
    embargo_bars: int = 12,
    param_grid: Optional[list] = None,
    objective_key: str = "total_return_pct",
    backtest_fn: Optional[BacktestFn] = None,
) -> WalkForwardReport:
    """Run walk-forward analysis over ``bars``.

    ``base_overrides`` is a dict of BacktestConfig field overrides applied to every
    run. ``param_grid`` (a list of override dicts) switches on anchored
    optimisation: each fold picks the grid entry with the best in-sample objective,
    then validates it out-of-sample. Without a grid, ``base_overrides`` is scored
    directly out-of-sample (pure rolling robustness).
    """
    base_overrides = dict(base_overrides or {})
    if backtest_fn is None:
        backtest_fn = _default_backtest_fn
    folds = make_folds(len(bars), n_folds, is_min_frac, min_oos_bars)

    for fold in folds:
        oos_bars = bars[fold.oos_start:fold.oos_end]
        if param_grid:
            # Audit fix #25: explicit embargo — exclude the last N in-sample
            # bars adjacent to the OOS block so indicator lookback windows
            # fitted in-sample cannot overlap the OOS data (the engine.py
            # walk_forward_backtest already had a two-sided embargo; this
            # brings the optimiser path in line).
            _is_end = max(fold.is_start + 1, fold.is_end - max(0, int(embargo_bars)))
            is_bars = bars[fold.is_start:_is_end]
            best_overrides, best_obj = None, None
            for grid in param_grid:
                cfg = {**base_overrides, **grid}
                res = await backtest_fn(is_bars, cfg)
                obj = _objective(res, objective_key)
                if best_obj is None or obj > best_obj:
                    best_obj, best_overrides = obj, cfg
            fold.chosen = dict(best_overrides or base_overrides)
            fold.is_objective = best_obj
        else:
            fold.chosen = dict(base_overrides)

        res = await backtest_fn(oos_bars, fold.chosen)
        fold.oos_objective = _objective(res, objective_key)
        fold.oos_return_pct = float(getattr(res, "total_return_pct", 0.0) or 0.0)
        fold.oos_win_rate = float(getattr(res, "win_rate", 0.0) or 0.0)
        fold.oos_trades = int(getattr(res, "total_trades", 0) or 0)
        fold.oos_sharpe = float(getattr(res, "sharpe_ratio", 0.0) or 0.0)
        fold.oos_max_dd = float(getattr(res, "max_drawdown_pct", 0.0) or 0.0)

    returns = [f.oos_return_pct for f in folds]
    oos_objs = [f.oos_objective for f in folds]
    is_objs = [f.is_objective for f in folds if f.is_objective is not None]
    overfitting_gap = (mean(is_objs) - mean(oos_objs)) if is_objs else None
    pct_profitable = sum(1 for r in returns if r > 0) / len(returns) if returns else 0.0

    inputs = {
        "pct_profitable_folds": pct_profitable,
        "mean_oos_return": mean(returns) if returns else 0.0,
        "overfitting_gap": overfitting_gap,
    }
    return WalkForwardReport(
        folds=folds,
        pct_profitable_folds=pct_profitable,
        mean_oos_return=mean(returns) if returns else 0.0,
        median_oos_return=median(returns) if returns else 0.0,
        std_oos_return=pstdev(returns) if len(returns) > 1 else 0.0,
        worst_oos_return=min(returns) if returns else 0.0,
        mean_oos_objective=mean(oos_objs) if oos_objs else 0.0,
        overfitting_gap=overfitting_gap,
        robustness=_classify(inputs),
    )


async def _default_backtest_fn(bars: list, overrides: dict):
    """Run the real deterministic backtester for a slice of bars."""
    from bot.backtest.engine import BacktestEngine
    from bot.backtest.models import BacktestConfig
    cfg = BacktestConfig(**{"use_llm": False, **overrides})
    engine = BacktestEngine(cfg)
    try:
        return await engine.run(bars)
    finally:
        engine.cleanup()
