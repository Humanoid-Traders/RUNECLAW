#!/usr/bin/env python3
"""Robustness suite: the measurement matrix every signal change must face.

Runs the honest portfolio backtest (strict data, next-open fills) across a
matrix of arms — baseline, walk-forward, hold-out universe, parameter
perturbations, and dark-flag re-validation — then extracts the numbers and
writes a markdown report.

This harness exists because ad-hoc measurement missed two critical bugs that
the matrix caught within one run each: backtests silently reading Bitget's
DEMO venue (different candles every bar, 8/10 hold-out symbols unlisted),
and the soft loss-streak gate latching permanently after 3 losses (a
production run froze for ~8 months of bars).

Usage:
    python scripts/robustness_suite.py                    # full matrix
    python scripts/robustness_suite.py --arms baseline,wf_std
    python scripts/robustness_suite.py --list             # show arms
    python scripts/robustness_suite.py --report-only      # re-parse outputs

Outputs land in --out (default data/robustness/<UTC date>): one .out per arm,
plus results.json and report.md.
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
HOLDOUT_SYMBOLS = ",".join(f"{b}/USDT:USDT" for b in (
    "UNI", "ATOM", "NEAR", "ARB", "OP", "FIL", "INJ", "APT", "SEI", "TIA"))

# name -> (symbols, extra runner args, env overrides, purpose)
ARMS: dict[str, tuple[str, list[str], dict[str, str], str]] = {
    "baseline": (STD_SYMBOLS, [], {}, "defaults on the development universe"),
    "wf_std": (STD_SYMBOLS, ["--walk-forward", "6"], {},
               "6-fold walk-forward, development universe"),
    "holdout_full": (HOLDOUT_SYMBOLS, [], {},
                     "defaults on 10 unseen symbols"),
    "holdout_wf": (HOLDOUT_SYMBOLS, ["--walk-forward", "6"], {},
                   "6-fold walk-forward, unseen symbols"),
    "conf_lo": (STD_SYMBOLS, [], {"MIN_CONFIDENCE": "0.50"},
                "confidence gate perturbed down"),
    "conf_hi": (STD_SYMBOLS, [], {"MIN_CONFIDENCE": "0.60"},
                "confidence gate perturbed up"),
    "cap_lo": (STD_SYMBOLS, [], {"CONFLUENCE_PATTERN_WEIGHT_CAP": "2.0"},
               "pattern weight cap perturbed down"),
    "cap_hi": (STD_SYMBOLS, [], {"CONFLUENCE_PATTERN_WEIGHT_CAP": "3.0"},
               "pattern weight cap perturbed up"),
    "arm_floor": (STD_SYMBOLS, [], {"MODE_MIN_CONFIDENCE_ENABLED": "true"},
                  "dark-flag re-validation: mode confidence floor ON"),
    "arm_trail": (STD_SYMBOLS, [], {"STRUCTURE_TRAIL_ENABLED": "true"},
                  "dark-flag re-validation: structure trail ON"),
    "arm_voters": (STD_SYMBOLS, [],
                   {"SMC_VOTERS_ENABLED": "true", "MFI_VOTER_ENABLED": "true",
                    "VOL_SPIKE_BAR_VOTE_ENABLED": "true"},
                   "dark-flag re-validation: SMC/MFI/vol-spike voters ON"),
}


def parse_full_run(text: str) -> dict:
    """Extract the summary metrics from a full-period runner output."""
    out: dict = {"kind": "full"}
    pats = {
        "return_pct": r"Total Return:\s*([+-]?[\d.]+)%",
        "trades": r"Total Trades:\s*(\d+)",
        "win_pct": r"Winners:\s*\d+\s*\((\d+)%\)",
        "profit_factor": r"Profit Factor:\s*([\d.]+)",
        "max_dd_pct": r"Max Drawdown:\s*([\d.]+)%",
    }
    for key, pat in pats.items():
        m = re.search(pat, text)
        out[key] = float(m.group(1)) if m else None
    out["skipped_symbols"] = re.findall(r"^\s*(\S+): fetch failed", text,
                                        re.MULTILINE)
    out["ok"] = out["return_pct"] is not None
    return out


def parse_walk_forward(text: str) -> dict:
    """Extract the fold table + summary line from a walk-forward output."""
    out: dict = {"kind": "walk_forward", "folds": []}
    for m in re.finditer(
            r"fold (\d+):\s*(\d+) trades\s+ret\s+([+-]?[\d.]+)%\s+win (\d+)%"
            r"\s+maxDD\s+([\d.]+)%\s+PF ([\d.]+)", text):
        out["folds"].append({
            "fold": int(m.group(1)), "trades": int(m.group(2)),
            "return_pct": float(m.group(3)), "win_pct": float(m.group(4)),
            "max_dd_pct": float(m.group(5)), "profit_factor": float(m.group(6)),
        })
    m = re.search(r"profitable folds (\d+)/(\d+) \| mean OOS ret ([+-]?[\d.]+)%"
                  r" \| worst ([+-]?[\d.]+)%", text)
    if m:
        out.update(profitable_folds=int(m.group(1)), total_folds=int(m.group(2)),
                   mean_oos_pct=float(m.group(3)), worst_fold_pct=float(m.group(4)))
    out["skipped_symbols"] = re.findall(r"^\s*(\S+): fetch failed", text,
                                        re.MULTILINE)
    out["ok"] = bool(out["folds"])
    return out


def run_arm(name: str, out_dir: str, limit: int, timeframe: str) -> dict:
    symbols, extra, env_over, purpose = ARMS[name]
    out_path = os.path.join(out_dir, f"{name}.out")
    cmd = [sys.executable, "-m", "bot.backtest.runner",
           "--symbols", symbols, "--fetch", "--limit", str(limit),
           "--timeframe", timeframe, "--honest", *extra]
    env = {**os.environ, **env_over}
    print(f"[suite] {name}: {purpose}" + (f" {env_over}" if env_over else ""))
    with open(out_path, "w") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT,
                              env=env, cwd=os.path.dirname(
                                  os.path.dirname(os.path.abspath(__file__))))
    text = open(out_path, errors="replace").read()
    parsed = (parse_walk_forward(text) if "--walk-forward" in extra
              else parse_full_run(text))
    parsed.update(arm=name, purpose=purpose, env=env_over,
                  exit_code=proc.returncode, output=out_path)
    if proc.returncode != 0 or not parsed["ok"]:
        # A crashed or unparseable arm is itself a robustness FINDING —
        # never silently dropped.
        tail = "\n".join(l for l in text.splitlines()
                         if '"channel"' not in l)[-2000:]
        parsed["failure_tail"] = tail
        print(f"[suite] {name}: FAILED (exit {proc.returncode}) — see report")
    return parsed


def write_report(results: list[dict], out_dir: str) -> str:
    lines = ["# Robustness suite report", "",
             f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
             "Honest portfolio backtest: strict production data, next-open fills.", ""]
    lines += ["| arm | purpose | trades | return | PF | win | maxDD | notes |",
              "|---|---|---|---|---|---|---|---|"]
    for r in results:
        if r["kind"] == "full":
            note = ("**CRASHED/UNPARSEABLE**" if not r["ok"] else
                    f"skipped: {','.join(r['skipped_symbols'])}"
                    if r["skipped_symbols"] else "")
            lines.append(
                f"| {r['arm']} | {r['purpose']} | {r.get('trades')} | "
                f"{r.get('return_pct')}% | {r.get('profit_factor')} | "
                f"{r.get('win_pct')}% | {r.get('max_dd_pct')}% | {note} |")
        else:
            note = ("**CRASHED/UNPARSEABLE**" if not r["ok"] else
                    f"{r.get('profitable_folds')}/{r.get('total_folds')} folds "
                    f"profitable, worst {r.get('worst_fold_pct')}%")
            lines.append(
                f"| {r['arm']} | {r['purpose']} | "
                f"{sum(f['trades'] for f in r['folds'])} | "
                f"mean OOS {r.get('mean_oos_pct')}% | — | — | — | {note} |")
    lines += ["", "## Fold detail", ""]
    for r in results:
        if r["kind"] != "walk_forward" or not r["folds"]:
            continue
        lines.append(f"### {r['arm']}")
        lines += ["| fold | trades | return | win | maxDD | PF |",
                  "|---|---|---|---|---|---|"]
        for f in r["folds"]:
            lines.append(f"| {f['fold']} | {f['trades']} | {f['return_pct']}% "
                         f"| {f['win_pct']}% | {f['max_dd_pct']}% "
                         f"| {f['profit_factor']} |")
        lines.append("")
    failures = [r for r in results if not r["ok"] or r.get("exit_code")]
    if failures:
        lines += ["## Failures (these are findings, not noise)", ""]
        for r in failures:
            lines += [f"### {r['arm']} (exit {r.get('exit_code')})", "```",
                      r.get("failure_tail", "")[-1500:], "```", ""]
    path = os.path.join(out_dir, "report.md")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--arms", default=",".join(ARMS),
                    help="comma list of arms to run (default: all)")
    ap.add_argument("--out", default=None,
                    help="output dir (default data/robustness/<UTC date>)")
    ap.add_argument("--limit", type=int, default=6000)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--list", action="store_true", help="list arms and exit")
    ap.add_argument("--report-only", action="store_true",
                    help="re-parse existing .out files in --out, skip running")
    args = ap.parse_args()

    if args.list:
        for name, (_, extra, env_over, purpose) in ARMS.items():
            print(f"  {name:14s} {purpose}"
                  + (f"  env={env_over}" if env_over else "")
                  + (" [walk-forward]" if extra else ""))
        return 0

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        print(f"unknown arms: {unknown}; use --list")
        return 2
    out_dir = args.out or os.path.join(
        "data", "robustness", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    os.makedirs(out_dir, exist_ok=True)

    results = []
    for name in arms:
        if args.report_only:
            path = os.path.join(out_dir, f"{name}.out")
            if not os.path.exists(path):
                continue
            text = open(path, errors="replace").read()
            parsed = (parse_walk_forward(text)
                      if "--walk-forward" in ARMS[name][1]
                      else parse_full_run(text))
            parsed.update(arm=name, purpose=ARMS[name][3], env=ARMS[name][2],
                          exit_code=0, output=path)
            results.append(parsed)
        else:
            results.append(run_arm(name, out_dir, args.limit, args.timeframe))

    with open(os.path.join(out_dir, "results.json"), "w") as fh:
        json.dump(results, fh, indent=2)
    report = write_report(results, out_dir)
    print(f"[suite] report: {report}")
    return 1 if any(not r["ok"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
