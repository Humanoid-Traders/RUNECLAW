#!/usr/bin/env python3
"""Per-voter ablation: measure each confluence voter's marginal contribution.

Runs the honest portfolio backtest once as a baseline, then once per voter
with that voter muted (ABLATE_VOTERS=<name>, the analyzer zeroes its weight),
and reports the delta. Reading the result:

  - removal HURTS (return/PF drop)  -> the voter CARRIES edge, keep it
  - removal HELPS (return/PF rise)  -> the voter is HARMFUL, candidate to dark
  - removal is ~neutral             -> the voter is dead weight, candidate to cut

This is the direct attack on the weak walk-forward: you can't tune what you
can't attribute. Ships a ranked table + results.json; no voter code changes.

Usage:
    python scripts/voter_ablation.py                 # curated voter set
    python scripts/voter_ablation.py --voters rsi,macd,vwap
    python scripts/voter_ablation.py --walk-forward  # ablate on 6-fold WF
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

STD_SYMBOLS = ",".join(f"{b}/USDT:USDT" for b in (
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "BNB"))

# The primary voters worth attributing (names as they appear in the confluence
# breakdown). Families (mtf_*, of_*, ew_*) are grouped where the harness can
# mute the whole family by listing its members.
CURATED = [
    "rsi", "macd", "ema_ribbon", "adx", "stochastic", "bollinger", "keltner",
    "donchian", "mfi", "obv", "candlestick", "chart_patterns", "reversal",
    "volume_profile", "liquidity_sweep", "divergence", "supply_demand",
    "vwap", "vwap_bands", "fibonacci", "mtf_structure", "mtf_bos",
    "of_cvd_trend", "taker", "volume_spike", "poc_magnet", "sentiment",
    "wyckoff", "harmonic", "elliott",
    # Tuning audit: the LIVE Elliott electorate votes under the typed labels
    # below — the legacy "elliott" label above only mutes the fallback voter
    # that fires when no typed pattern exists, so the 2026-07-03 ablation
    # never actually attributed Elliott. fib_extension and candles_mtf were
    # likewise absent and have zero drop-one evidence.
    "ew_impulse", "ew_corrective", "ew_diagonal", "ew_wxy", "ew_mtf_align",
    "fib_extension", "candles_mtf",
    # Tuning audit round 2: SMC/structure + order-flow voters that likewise
    # had zero drop-one attribution.
    "fvg", "premium_discount", "mtf_choch", "mtf_alignment",
    "of_funding", "of_whale_bias", "of_book_imbalance", "of_cvd_divergence",
    "of_spot_futures_div", "of_oi_price_div",
    # Divergence sub-labels — live only under DIVERGENCE_SUBLABELS_ENABLED.
    "divergence_rsi", "divergence_macd", "divergence_obv",
]

_PATS = {
    "return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
    "trades": r"Total Trades:\s*(\d+)",
    "profit_factor": r"Profit Factor:\s*([\d.]+)",
    "sharpe": r"Sharpe Ratio:\s*([+-]?[\d.]+)",
    "win_pct": r"Winners:\s*\d+\s*\((\d+)%\)",
}
_WF = r"profitable folds (\d+)/(\d+) \| mean OOS ret ([+-]?[\d.]+)%"


def _parse(text: str, wf: bool) -> dict:
    out: dict = {}
    if wf:
        m = re.search(_WF, text)
        if m:
            out = {"profitable_folds": int(m.group(1)), "total_folds": int(m.group(2)),
                   "mean_oos_pct": float(m.group(3))}
        out["ok"] = bool(m)
        return out
    for k, p in _PATS.items():
        m = re.search(p, text)
        out[k] = float(m.group(1)) if m else None
    out["ok"] = out.get("return_pct") is not None
    return out


def _run(ablate: str, out_dir: str, wf: bool, limit: int) -> dict:
    label = ablate or "baseline"
    path = os.path.join(out_dir, f"{label}.out")
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--symbols", STD_SYMBOLS,
           "--fetch", "--limit", str(limit), "--timeframe", "1h", "--honest"]
    if wf:
        cmd += ["--walk-forward", "6"]
    env = {**os.environ}
    if ablate:
        env["ABLATE_VOTERS"] = ablate
    else:
        env.pop("ABLATE_VOTERS", None)
    print(f"[ablation] {label} ...")
    with open(path, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parsed = _parse(open(path, errors="replace").read(), wf)
    parsed.update(voter=label, exit_code=proc.returncode)
    return parsed


def _verdict(base: dict, arm: dict, wf: bool) -> str:
    if not arm.get("ok"):
        return "run failed"
    if wf:
        d = arm["mean_oos_pct"] - base["mean_oos_pct"]
        if d < -0.15:
            return "CARRIES edge (removal hurt OOS)"
        if d > 0.15:
            return "HARMFUL (removal helped OOS)"
        return "~neutral"
    d = (arm["return_pct"] or 0) - (base["return_pct"] or 0)
    if d < -0.15:
        return "CARRIES edge (removal hurt)"
    if d > 0.15:
        return "HARMFUL (removal helped)"
    return "~neutral / dead weight"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--voters", default=",".join(CURATED))
    ap.add_argument("--walk-forward", action="store_true")
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    voters = [v.strip() for v in args.voters.split(",") if v.strip()]
    out_dir = args.out or os.path.join(
        "data", "ablation", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(out_dir, exist_ok=True)

    base = _run("", out_dir, args.walk_forward, args.limit)
    results = [base]
    for v in voters:
        results.append(_run(v, out_dir, args.walk_forward, args.limit))

    # Ranked report: most-improved-by-removal first (harmful), then neutral,
    # then edge-carriers last.
    lines = ["# Voter ablation report",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
             ""]
    if args.walk_forward:
        lines.append(f"Baseline: {base.get('profitable_folds')}/{base.get('total_folds')} "
                     f"folds, mean OOS {base.get('mean_oos_pct')}%")
        lines += ["", "| voter | mean OOS | Δ vs base | verdict |", "|---|---|---|---|"]
        for r in results[1:]:
            if not r.get("ok"):
                lines.append(f"| {r['voter']} | — | — | run failed |"); continue
            d = r["mean_oos_pct"] - base["mean_oos_pct"]
            lines.append(f"| {r['voter']} | {r['mean_oos_pct']}% | {d:+.2f}pp "
                         f"| {_verdict(base, r, True)} |")
    else:
        lines.append(f"Baseline: {base.get('return_pct')}% / PF {base.get('profit_factor')} "
                     f"/ {base.get('trades')} trades")
        lines += ["", "| voter | return | Δ vs base | trades | PF | verdict |",
                  "|---|---|---|---|---|---|"]
        ranked = sorted(results[1:], key=lambda r: -((r.get("return_pct") or -99)
                                                      - (base.get("return_pct") or 0)))
        for r in ranked:
            if not r.get("ok"):
                lines.append(f"| {r['voter']} | — | — | — | — | run failed |"); continue
            d = (r["return_pct"] or 0) - (base["return_pct"] or 0)
            lines.append(f"| {r['voter']} | {r['return_pct']}% | {d:+.2f}pp "
                         f"| {r['trades']} | {r['profit_factor']} | {_verdict(base, r, False)} |")

    report = os.path.join(out_dir, "report.md")
    with open(report, "w") as fh:
        fh.write("\n".join(lines))
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    print(f"[ablation] report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
