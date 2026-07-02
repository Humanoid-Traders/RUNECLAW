#!/usr/bin/env python3
"""
Activation flag comparison harness — legacy (all deep-audit flags OFF) vs new
(all ON), on identical synthetic data, across multiple seeds.

All 23 deep-audit flags now default ON in code (see docs/FLAG_ACTIVATION.md). This
harness makes the legacy-vs-new comparison REPRODUCIBLE in-repo instead of an
ad-hoc one-off: it runs the deterministic backtester twice per seed — once with
the activation flags forced OFF, once forced ON — over the same generated bars,
and aggregates the metric deltas so the effect of the activation is auditable.

It toggles the two kinds of flag the backtester actually exercises:
  * env-read flags (chart-pattern + order-flow modules read os.environ), and
  * CONFIG-attr flags (analyzer / learning / risk sections),
matching how the live bot resolves them. Order-flow (OF_*) and learning nudges
have little/no effect on synthetic data (no L2 book, no accumulated history) — the
robust, repeatable signal is the risk-sizing change (per-strategy cap + regime
sizing). See the module docstrings for the honest caveats.

Usage:
    python scripts/flag_compare.py                      # 8 seeds, single backtest
    python scripts/flag_compare.py --seeds 15 --bars 2160
    python scripts/flag_compare.py --walk-forward 5     # OOS comparison per seed
    python scripts/flag_compare.py --json out.json      # machine-readable dump
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
from pathlib import Path

# Make `import bot...` work regardless of the cwd the script is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import CONFIG  # noqa: E402
from bot.backtest.data_loader import DataLoader  # noqa: E402
from bot.backtest.engine import BacktestEngine  # noqa: E402
from bot.backtest.models import BacktestConfig  # noqa: E402

# Activation flags the backtest path actually reads. env-read flags live on the
# chart-pattern / order-flow modules; CFG flags live on frozen CONFIG sections.
ENV_FLAGS = (
    "PATTERN_ATR_TOLERANCES_ENABLED",
    "LEADING_DIAGONAL_PRETREND_FIX",
    "LIQUIDITY_SWEEP_OWN_CLOSE",
    "OF_FUNDING_VOTE_FIXED_SCALE",
    "OF_TIME_BARS_ENABLED",
)
CFG_FLAGS = (
    ("analyzer", "vwap_session_anchored"),
    ("analyzer", "vwap_bands_vote_enabled"),
    ("analyzer", "vwap_slope_vote_enabled"),
    ("analyzer", "vwap_setup_anchoring_enabled"),
    ("analyzer", "vwap_anchored_pivot_enabled"),
    ("analyzer", "setup_expectancy_enabled"),
    ("analyzer", "confidence_calibration_enabled"),
    ("analyzer", "learning_auto_refit_enabled"),
    ("learning", "learn_from_paper_closes_enabled"),
    ("learning", "adaptive_confidence_enabled"),
    ("risk", "regime_sizing_enabled"),
    ("risk", "per_strategy_notional_cap_enabled"),
    ("risk", "daily_loss_breaker_autoreset_enabled"),
)

# Metrics aggregated for the legacy-vs-new comparison. "_notional" is synthesised
# from the per-trade size_usd (sum of position notional opened over the run).
METRICS = (
    "total_trades", "win_rate", "total_return_pct", "net_pnl", "sharpe_ratio",
    "sortino_ratio", "max_drawdown_pct", "profit_factor", "risk_reward_avg",
    "_notional",
)


def apply_flags(on: bool) -> None:
    """Force every activation flag ON/OFF. Mutates process env (for the module
    env-reads) and the frozen CONFIG singletons (for the section flags). This is a
    one-shot CLI that exits after running, so mutating the global is acceptable."""
    import os
    for key in ENV_FLAGS:
        os.environ[key] = "1" if on else "0"
    for section, name in CFG_FLAGS:
        # CONFIG sections are frozen dataclasses → object.__setattr__ to override.
        object.__setattr__(getattr(CONFIG, section), name, on)


def _backtest_config() -> BacktestConfig:
    return BacktestConfig(symbol="BTC/USDT", initial_balance=10000.0,
                          commission_pct=0.1, slippage_pct=0.05)


async def _run_backtest(bars: list) -> dict:
    eng = BacktestEngine(_backtest_config())
    res = await eng.run(bars)
    eng.cleanup()
    d = res.model_dump()
    d["_notional"] = sum(t.get("size_usd", 0) for t in d.get("trades", []))
    return d


async def _run_walk_forward(bars: list, n_folds: int) -> dict:
    from bot.backtest.walk_forward import run_walk_forward
    rep = await run_walk_forward(
        bars, {"initial_balance": 10000.0, "commission_pct": 0.1, "slippage_pct": 0.05},
        n_folds=n_folds)
    return {
        "mean_oos_return": rep.mean_oos_return,
        "median_oos_return": rep.median_oos_return,
        "worst_oos_return": rep.worst_oos_return,
        "std_oos_return": rep.std_oos_return,
        "pct_profitable_folds": rep.pct_profitable_folds,
        "robustness": rep.robustness.split()[0],
    }


async def compare_backtest(seeds: list[int], bars_n: int) -> dict:
    """Run legacy-vs-new single-backtest comparison over `seeds`. Returns a dict
    with per-seed rows and the aggregate means/deltas."""
    rows = {m: [] for m in METRICS}
    per_seed = []
    for s in seeds:
        bars = DataLoader.generate_synthetic(bars=bars_n, seed=s)
        apply_flags(False)
        legacy = await _run_backtest(bars)
        apply_flags(True)
        new = await _run_backtest(bars)
        for m in METRICS:
            rows[m].append((legacy.get(m) or 0, new.get(m) or 0))
        per_seed.append({
            "seed": s,
            "legacy_return_pct": legacy["total_return_pct"],
            "new_return_pct": new["total_return_pct"],
            "legacy_sharpe": legacy["sharpe_ratio"],
            "new_sharpe": new["sharpe_ratio"],
            "legacy_notional": legacy["_notional"],
            "new_notional": new["_notional"],
            "legacy_trades": legacy["total_trades"],
            "new_trades": new["total_trades"],
        })
    agg = {}
    for m in METRICS:
        legacy_vals = [a for a, _ in rows[m]]
        new_vals = [b for _, b in rows[m]]
        agg[m] = {
            "legacy_mean": st.mean(legacy_vals),
            "new_mean": st.mean(new_vals),
            "delta": st.mean(new_vals) - st.mean(legacy_vals),
            "new_ge_legacy": sum(1 for a, b in rows[m] if b >= a),
            "n": len(rows[m]),
        }
    return {"mode": "backtest", "seeds": seeds, "per_seed": per_seed, "aggregate": agg}


async def compare_walk_forward(seeds: list[int], bars_n: int, n_folds: int) -> dict:
    """Run legacy-vs-new N-fold walk-forward comparison over `seeds`."""
    keys = ("mean_oos_return", "median_oos_return", "worst_oos_return",
            "std_oos_return", "pct_profitable_folds")
    legacy_agg = {k: [] for k in keys}
    new_agg = {k: [] for k in keys}
    per_seed = []
    for s in seeds:
        bars = DataLoader.generate_synthetic(bars=bars_n, seed=s)
        apply_flags(False)
        legacy = await _run_walk_forward(bars, n_folds)
        apply_flags(True)
        new = await _run_walk_forward(bars, n_folds)
        for k in keys:
            legacy_agg[k].append(legacy[k])
            new_agg[k].append(new[k])
        per_seed.append({"seed": s, "legacy": legacy, "new": new})
    aggregate = {k: {"legacy_mean": st.mean(legacy_agg[k]),
                     "new_mean": st.mean(new_agg[k]),
                     "delta": st.mean(new_agg[k]) - st.mean(legacy_agg[k])}
                 for k in keys}
    return {"mode": "walk_forward", "n_folds": n_folds, "seeds": seeds,
            "per_seed": per_seed, "aggregate": aggregate}


def _print_backtest(report: dict) -> None:
    print(f"\nLegacy (all OFF) vs New (all ON) — {len(report['seeds'])} seeds, single backtest\n")
    for r in report["per_seed"]:
        print(f"seed {r['seed']:>2}: ret {r['legacy_return_pct']:+6.2f}->{r['new_return_pct']:+6.2f}  "
              f"sharpe {r['legacy_sharpe']:5.2f}->{r['new_sharpe']:5.2f}  "
              f"notional {r['legacy_notional']:.0f}->{r['new_notional']:.0f}  "
              f"trades {r['legacy_trades']}->{r['new_trades']}")
    print(f"\n=== aggregate (mean legacy -> mean new, delta, #seeds new>=legacy) ===")
    print(f"{'metric':<20}{'legacy':>10}{'new':>10}{'delta':>10}{'new>=leg':>10}")
    for m in METRICS:
        a = report["aggregate"][m]
        print(f"{m:<20}{a['legacy_mean']:>10.4g}{a['new_mean']:>10.4g}"
              f"{a['delta']:>+10.4g}{a['new_ge_legacy']:>8}/{a['n']}")


def _print_walk_forward(report: dict) -> None:
    nf = report["n_folds"]
    print(f"\n{nf}-fold walk-forward, legacy(OFF) vs new(ON) — "
          f"{len(report['seeds'])} seeds = {len(report['seeds']) * nf} OOS blocks\n")
    for r in report["per_seed"]:
        lo, nw = r["legacy"], r["new"]
        print(f"seed {r['seed']:>2}: OOS mean ret {lo['mean_oos_return']:+5.2f}%->{nw['mean_oos_return']:+5.2f}%  "
              f"prof {lo['pct_profitable_folds']:.0%}->{nw['pct_profitable_folds']:.0%}  "
              f"[{lo['robustness']}->{nw['robustness']}]")
    print(f"\n=== aggregate (mean legacy -> mean new, delta) ===")
    print(f"{'OOS metric':<22}{'legacy':>10}{'new':>10}{'delta':>10}")
    for k, a in report["aggregate"].items():
        print(f"{k:<22}{a['legacy_mean']:>10.4g}{a['new_mean']:>10.4g}{a['delta']:>+10.4g}")


def main() -> None:
    p = argparse.ArgumentParser(description="Legacy-vs-new activation flag backtest comparison")
    p.add_argument("--seeds", type=int, default=8, help="Number of seeds (1..N). Default 8.")
    p.add_argument("--bars", type=int, default=2160, help="Synthetic bars per seed. Default 2160.")
    p.add_argument("--walk-forward", type=int, metavar="N", default=0,
                   help="Run N-fold walk-forward comparison instead of a single backtest.")
    p.add_argument("--json", type=str, metavar="PATH", help="Also write the report as JSON.")
    args = p.parse_args()

    seeds = list(range(1, args.seeds + 1))
    if args.walk_forward and args.walk_forward > 0:
        report = asyncio.run(compare_walk_forward(seeds, args.bars, args.walk_forward))
        _print_walk_forward(report)
    else:
        report = asyncio.run(compare_backtest(seeds, args.bars))
        _print_backtest(report)

    print("\nNote: synthetic GBM data — no L2 book / no history, so OF_* and learning"
          "\nflags barely move; the robust effect is the risk-sizing (per-strategy cap +"
          "\nregime sizing) shrinking notional. Not a real-edge measurement.")

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\nJSON written to {args.json}")


if __name__ == "__main__":
    main()
