# Walk-Forward Analysis

Offline robustness check: **is the strategy's edge real out-of-sample, or
curve-fit?** A single backtest is easy to overfit — parameters that shine on one
period may just be memorising it. Walk-forward evaluates on data the parameters
never saw.

> Analysis only. Never trades, never touches live state; runs the same
> deterministic backtester (`use_llm=False`) as the rest of the suite.

## Two modes

- **Rolling robustness** (no param grid) — split the series into sequential
  out-of-sample folds and backtest each independently. Consistency across folds =
  robust; a couple of lucky folds carrying the rest = fragile.
- **Anchored optimisation** (with a param grid) — for each fold, pick the best
  parameters on the **in-sample** history, then score them on the next, **unseen**
  out-of-sample block. The **`overfitting_gap`** (mean in-sample − mean
  out-of-sample objective) is the headline overfit signal.

## CLI

```bash
# 4-fold rolling robustness on real data
python -m bot.backtest.runner --limit 1500 --walk-forward 4

# anchored optimisation: sweep confidence_threshold IS, validate OOS
python -m bot.backtest.runner --limit 1500 --walk-forward 4 --wf-optimize
```

Output reports, per fold: out-of-sample return, win rate, trades, Sharpe, max
drawdown (and the chosen threshold when optimising), plus an aggregate line:
profitable-fold %, OOS return mean/median/std, worst fold, overfit gap, and a
robustness verdict (`ROBUST` / `MIXED` / `FRAGILE` / `OVERFIT`).

## Library

```python
from bot.backtest.walk_forward import run_walk_forward
report = await run_walk_forward(
    bars, base_overrides={"symbol": "BTC/USDT"},
    n_folds=4, param_grid=[{"confidence_threshold": t} for t in (0.45, 0.5, 0.55)],
)
print(report.summary())
```

`make_folds()` builds anchored (expanding-in-sample) folds; `run_walk_forward()`
accepts an injectable `backtest_fn` so the fold/aggregation logic is unit-tested
without the heavy engine (`tests/test_walk_forward.py`).

## Reading the verdict

- **ROBUST** — ≥70% of folds profitable, positive mean out-of-sample return, no
  large overfit gap.
- **MIXED** — some out-of-sample edge; watch consistency.
- **FRAGILE** — out-of-sample edge not established (mean ≤ 0 or <50% folds profit).
- **OVERFIT** — in-sample looks great but out-of-sample doesn't follow (large gap).

Use it to validate the learning overlays (calibration / expectancy / voter
weights) and any parameter change before trusting it live.
