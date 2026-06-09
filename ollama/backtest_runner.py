"""
RUNECLAW Backtest — Runner & Walk-Forward
Bridges the model layer to the simulation layer:

  1. Takes the model's text/JSON trade ideas (same format the eval parses)
  2. Anchors each to a decision timestamp on real OHLCV
  3. Runs them through the execution engine
  4. Computes after-cost metrics
  5. Orchestrates WALK-FORWARD analysis — the real overfitting guard

Why walk-forward matters: a single out-of-sample number can still be a fluke of
one period. Walk-forward splits the OOS data into consecutive windows and checks
whether the edge HOLDS ACROSS windows. An edge that only appears in one window
and vanishes in the others is overfit, not real.
"""

import json
import re
import argparse
import sys
from pathlib import Path
import pandas as pd
import numpy as np

from backtest_data import load_ohlcv, synth_ohlcv, out_of_sample_split, OHLCVData
from backtest_engine import TradeIdea, run_portfolio, simulate_trade, CostModel, Outcome
from backtest_metrics import compute_metrics, print_metrics, BacktestMetrics


# ── Parse model output into a TradeIdea (reuses the eval's tolerant approach) ──
def parse_trade_idea(text: str, symbol: str, decision_ts: pd.Timestamp,
                     default_risk_pct: float = 2.0):
    """Extract entry/SL/TP/direction from model output (JSON or text format)."""
    def num(pattern):
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            return None
        raw = m.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None

    # Try JSON first
    data = {}
    jm = re.search(r"\{.*\}", text, re.DOTALL)
    if jm:
        try:
            data = json.loads(jm.group(0))
        except json.JSONDecodeError:
            data = {}

    direction = str(data.get("direction", "")).upper()
    if direction not in ("LONG", "SHORT"):
        dm = re.search(r"\b(LONG|SHORT)\b", text, re.IGNORECASE)
        direction = dm.group(1).upper() if dm else None

    entry = data.get("entry_price") or num(r"entry[_\s]?(?:price)?[:\s]+([\d,\.]+)")
    sl    = data.get("stop_loss")   or num(r"stop[_\s]?loss[:\s]+([\d,\.]+)") or num(r"\bSL[:\s]+([\d,\.]+)")
    tp    = data.get("take_profit") or num(r"take[_\s]?profit[:\s]*1?[:\s]+([\d,\.]+)") or num(r"\bTP1?[:\s]+([\d,\.]+)")
    conf  = data.get("confidence")  or num(r"confidence(?:\s+score)?[:\s]+([\d\.]+)") or 0.0

    if not all([direction, entry, sl, tp]):
        return None
    try:
        return TradeIdea(
            symbol=symbol, direction=direction,
            entry_price=float(entry), stop_loss=float(sl), take_profit=float(tp),
            decision_ts=decision_ts, confidence=float(conf or 0),
            risk_pct=default_risk_pct,
        )
    except (TypeError, ValueError):
        return None


def walk_forward(ideas: list, data_by_symbol: dict, n_windows: int = 4,
                 starting_equity: float = 10_000.0, costs: CostModel = None):
    """
    Split filled trades into n consecutive time windows and compute metrics per
    window. Reports whether expectancy is positive AND consistent across windows.
    """
    costs = costs or CostModel()
    ordered = sorted(ideas, key=lambda x: x.decision_ts)
    if len(ordered) < n_windows * 10:
        print(f"  ⚠ Only {len(ordered)} trades — walk-forward with {n_windows} windows "
              f"will be noisy. Need ~{n_windows*10}+ for meaningful per-window reads.")

    windows = np.array_split(ordered, n_windows)
    window_metrics = []
    print(f"\n  WALK-FORWARD ({n_windows} consecutive windows)")
    print(f"  {'window':<8}{'trades':>8}{'expectancy':>13}{'PF':>8}{'win%':>8}")
    print(f"  {'-'*45}")
    for i, w in enumerate(windows, 1):
        w = list(w)
        res = run_portfolio(w, data_by_symbol, starting_equity, costs)
        m = compute_metrics(res, starting_equity)
        window_metrics.append(m)
        span = f"{w[0].decision_ts.date()}" if w else "—"
        print(f"  {i:<8}{m.n_filled:>8}{m.expectancy_r:>+13.4f}{m.profit_factor:>8.2f}{m.win_rate*100:>7.1f}%")

    expectancies = [m.expectancy_r for m in window_metrics if m.n_filled > 0]
    positive_windows = sum(1 for e in expectancies if e > 0)
    print(f"  {'-'*45}")
    print(f"  Positive-expectancy windows: {positive_windows}/{len(expectancies)}")
    if expectancies:
        consistency = np.std(expectancies)
        print(f"  Expectancy std across windows: {consistency:.4f}")
        if positive_windows == len(expectancies):
            print(f"  ✓ Edge HOLDS across all windows — promising consistency.")
        elif positive_windows >= len(expectancies) * 0.75:
            print(f"  ~ Edge mostly holds but wobbles. Investigate the weak window.")
        else:
            print(f"  ✗ Edge does NOT hold across windows — likely overfit or regime-dependent.")
    return window_metrics


def run_backtest(ideas: list, data_by_symbol: dict, starting_equity: float = 10_000.0,
                 costs: CostModel = None, do_walk_forward: bool = True,
                 n_windows: int = 4):
    costs = costs or CostModel()
    results = run_portfolio(ideas, data_by_symbol, starting_equity, costs)
    metrics = compute_metrics(results, starting_equity)
    print_metrics(metrics)

    # Cost-sensitivity: re-run at 0 and 2x costs to see fragility
    zero = compute_metrics(run_portfolio(ideas, data_by_symbol, starting_equity, CostModel(0,0)), starting_equity)
    double = compute_metrics(run_portfolio(ideas, data_by_symbol, starting_equity,
                             CostModel(costs.fee_bps*2, costs.slippage_bps*2)), starting_equity)
    print(f"  COST SENSITIVITY (expectancy R):")
    print(f"    0 bps      : {zero.expectancy_r:+.4f}")
    print(f"    {costs.fee_bps:.0f}/{costs.slippage_bps:.0f} bps : {metrics.expectancy_r:+.4f}  (your assumption)")
    print(f"    2x cost    : {double.expectancy_r:+.4f}")
    if zero.expectancy_r > 0 and double.expectancy_r <= 0:
        print(f"    ⚠ Edge disappears under higher costs — fragile. The 'edge' may be cost-assumption.")
    print()

    if do_walk_forward:
        walk_forward(ideas, data_by_symbol, n_windows, starting_equity, costs)

    return results, metrics


def results_to_json(results, metrics: BacktestMetrics, path: str):
    payload = {
        "metrics": {k: v for k, v in metrics.__dict__.items()},
        "verdict": metrics.verdict(),
        "trades": [
            {
                "symbol": r.idea.symbol, "direction": r.idea.direction,
                "outcome": r.outcome.value,
                "entry_ts": str(r.entry_ts) if r.entry_ts else None,
                "exit_ts": str(r.exit_ts) if r.exit_ts else None,
                "r_multiple": round(r.r_multiple, 3),
                "net_return_pct": round(r.net_return_pct, 3),
                "pnl_quote": round(r.pnl_quote, 2),
                "bars_held": r.bars_held,
                "confidence": r.idea.confidence,
            }
            for r in results
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2))
    print(f"  Backtest results → {path}")


if __name__ == "__main__":
    # Demo on synthetic data with a simple momentum rule standing in for the model.
    # (Replace this block with real model ideas + real OHLCV.)
    print("DEMO: synthetic data, momentum-rule trades (stand-in for model output)")
    data = synth_ohlcv(n_bars=4000, regime="mixed", seed=11)
    in_s, oos = out_of_sample_split(data, holdout_frac=0.3)

    # Build trades on OOS data only, from a trivial momentum rule
    df = oos.df
    ideas = []
    for i in range(20, len(df) - 5, 15):
        window = df["close"].iloc[i-20:i]
        mom = window.iloc[-1] / window.iloc[0] - 1
        px = df["close"].iloc[i]
        ts = df.index[i]
        if mom > 0.02:
            ideas.append(TradeIdea("TEST/USDT","LONG", px, px*0.97, px*1.06, ts))
        elif mom < -0.02:
            ideas.append(TradeIdea("TEST/USDT","SHORT", px, px*1.03, px*0.94, ts))

    run_backtest(ideas, {"TEST/USDT": oos}, 10_000, CostModel(5, 3), n_windows=4)
