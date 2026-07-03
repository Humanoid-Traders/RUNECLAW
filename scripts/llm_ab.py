#!/usr/bin/env python3
"""A/B the LLM confidence blend: does the 60% LLM contribution beat pure
confluence out-of-sample?

CRITICAL CONTEXT (the finding this harness exists to resolve): every honest
backtest runs LLM-free — the runner defaults to rule-based confidence and
there is no recorded LLM to replay — so all measured numbers reflect
LLM_BLEND_WEIGHT effectively 0. LIVE runs at LLM_BLEND_WEIGHT=0.6, the LLM
driving 60% of confidence. The strategy validated in backtest is therefore
NOT the strategy running live. This harness closes that gap by replaying the
bot's own recorded live theses at 0.6 vs 0.0.

It is DATA-GATED: it needs data/learning/llm_calibration.jsonl, which the
analyzer appends to on every LIVE/paper decision (as_of is None). Run the bot
live/paper with an LLM configured until the file has a few hundred entries,
then run this.

Usage:
    python scripts/llm_ab.py                 # 0.6 vs 0.0 on the honest suite
    python scripts/llm_ab.py --weights 0.6,0.4,0.0
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STD_SYMBOLS = ",".join(f"{b}/USDT:USDT" for b in (
    "BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX", "LTC", "BNB"))
LLM_LOG = Path("data/learning/llm_calibration.jsonl")

_PATS = {
    "return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
    "trades": r"Total Trades:\s*(\d+)",
    "profit_factor": r"Profit Factor:\s*([\d.]+)",
    "sharpe": r"Sharpe Ratio:\s*([+-]?[\d.]+)",
}


def _parse(text: str) -> dict:
    out = {k: (float(m.group(1)) if (m := re.search(p, text)) else None)
           for k, p in _PATS.items()}
    out["ok"] = out["return_pct"] is not None
    return out


def _recorded_count() -> int:
    if not LLM_LOG.exists():
        return 0
    try:
        return sum(1 for line in LLM_LOG.read_text().splitlines() if line.strip())
    except Exception:
        return 0


def _run(weight: float, out_dir: str, limit: int) -> dict:
    path = os.path.join(out_dir, f"llm_w{weight}.out")
    env = {**os.environ, "LLM_BLEND_WEIGHT": str(weight)}
    cmd = [sys.executable, "-m", "bot.backtest.runner", "--symbols", STD_SYMBOLS,
           "--fetch", "--limit", str(limit), "--timeframe", "1h", "--honest",
           "--use-recorded-llm"]
    print(f"[llm-ab] blend weight {weight} ...")
    with open(path, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env,
                              cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parsed = _parse(open(path, errors="replace").read())
    parsed.update(weight=weight, exit_code=proc.returncode)
    return parsed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--weights", default="0.6,0.0")
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--out", default=None)
    ap.add_argument("--min-records", type=int, default=100)
    args = ap.parse_args()

    n = _recorded_count()
    if n < args.min_records:
        print("=" * 68)
        print("LLM A/B is DATA-GATED and cannot run a meaningful comparison yet.")
        print(f"  recorded LLM theses: {n} (need >= {args.min_records})")
        print(f"  file: {LLM_LOG}")
        print()
        print("FINDING (holds until data accrues): every honest backtest to date")
        print("has run LLM-FREE (rule-based confidence, no recorded LLM to replay),")
        print("so all measured numbers reflect LLM blend weight ~0. LIVE runs at")
        print("LLM_BLEND_WEIGHT=0.6. The strategy validated in backtest is NOT the")
        print("strategy running live — the 60% LLM contribution is unmeasured.")
        print()
        print("To enable: run the bot live/paper with an LLM configured until")
        print(f"{LLM_LOG} has >= {args.min_records} entries, then re-run this.")
        print("=" * 68)
        return 2

    weights = [float(w) for w in args.weights.split(",")]
    out_dir = args.out or os.path.join(
        "data", "llm_ab", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(out_dir, exist_ok=True)
    results = [_run(w, out_dir, args.limit) for w in weights]

    lines = ["# LLM blend A/B", f"Recorded theses replayed: {n}", "",
             "| LLM weight | return | PF | Sharpe | trades |", "|---|---|---|---|---|"]
    for r in results:
        if not r["ok"]:
            lines.append(f"| {r['weight']} | run failed | | | |"); continue
        lines.append(f"| {r['weight']} | {r['return_pct']}% | {r['profit_factor']} "
                     f"| {r['sharpe']} | {r['trades']} |")
    report = os.path.join(out_dir, "report.md")
    Path(report).write_text("\n".join(lines))
    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump({"recorded_theses": n, "results": results}, fh, indent=2)
    print(f"[llm-ab] report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
