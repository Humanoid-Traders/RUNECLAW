#!/usr/bin/env python3
"""Generate committed, reproducible per-agent benchmark scorecards.

For each real marketplace Strategy-Agent (``RunStrategySkill.PRESETS``) this runs
the engine's HONEST frozen-benchmark backtester with that agent's real entry
gates (confidence / symbols / volume-spike / regime / RSI — see Phase 2a) and
writes a percent/ratio-only scorecard to ``data/benchmark/scorecards/<slug>.json``.

§4-safe by construction: only percent/ratio metrics are recorded — never a
dollar figure. Every scorecard is stamped with the dataset name + ``dataset_hash``
+ bar count + the exact gates, so anyone can re-run the identical backtest (in
the web Strategy Lab or via ``python -m bot.backtest.runner``) and reproduce it.

Usage:
    python -m scripts.gen_agent_scorecards            # default dataset/symbols
    python -m scripts.gen_agent_scorecards --dataset data/benchmark/majors_1h \
        --symbols BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT --last-bars 1500

This is an OFFLINE batch (frozen data, no network). Regenerate + commit whenever
the presets or the benchmark snapshot change.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "benchmark" / "scorecards"

# Percent/ratio metrics only — NEVER a dollar field (§4). These are the keys
# copied verbatim from the backtester's result into the public scorecard.
_METRIC_KEYS = (
    "total_return_pct", "profit_factor", "win_rate", "max_drawdown_pct",
    "sharpe_ratio", "sortino_ratio", "calmar_ratio", "total_trades",
)


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(key).lower()).strip("-")


def _gate_args(cfg: dict) -> list[str]:
    """Map a preset's real filters onto the runner's Phase-2a gate flags.
    Only the gates the backtester faithfully models are emitted; ``sl_atr_mult`` /
    ``tp_atr_mult`` are recorded as 'unmodeled' by the caller, not applied."""
    args: list[str] = []
    if cfg.get("confidence_threshold") is not None:
        args += ["--confidence-threshold", str(cfg["confidence_threshold"])]
    if cfg.get("volume_spike_min") is not None:
        args += ["--volume-spike-min", str(cfg["volume_spike_min"])]
    if cfg.get("regime"):
        args += ["--regime-filter", str(cfg["regime"])]
    if cfg.get("rsi_threshold") is not None:
        args += ["--rsi-max", str(cfg["rsi_threshold"])]
    return args


def _run_one(preset_key: str, cfg: dict, dataset: str, symbols: str,
             last_bars: int) -> dict:
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        out_path = tf.name
    cmd = [
        sys.executable, "-m", "bot.backtest.runner",
        "--dataset", dataset, "--symbols", symbols,
        "--last-bars", str(last_bars), "--honest", "--strict-data",
        "-o", out_path,
    ] + _gate_args(cfg)
    print(f"  [{preset_key}] {' '.join(cmd[4:])}")
    subprocess.run(cmd, check=True, cwd=str(REPO),
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
                   timeout=590)
    with open(out_path) as fh:
        res = json.load(fh)
    Path(out_path).unlink(missing_ok=True)
    return res


def generate(dataset: str, symbols: str, last_bars: int) -> list[str]:
    from bot.skills.skill_registry import RunStrategySkill
    from bot.backtest import snapshot as _snap

    man = _snap.load_manifest_multi(dataset)
    dataset_hash = man.get("dataset_hash", "")
    dataset_name = Path(dataset).name
    sym_list = [s.strip() for s in symbols.split(",") if s.strip()]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for key, cfg in RunStrategySkill.PRESETS.items():
        res = _run_one(key, cfg, dataset, symbols, last_bars)
        metrics = {}
        for mk in _METRIC_KEYS:
            v = res.get(mk)
            metrics[mk] = round(v, 4) if isinstance(v, (int, float)) else v
        # Exit-geometry knobs the backtester doesn't model per-run are disclosed,
        # never silently dropped (honest scoping — see Phase 2a).
        unmodeled = [k for k in ("sl_atr_mult", "tp_atr_mult")
                     if cfg.get(k) is not None]
        card = {
            "format": "runeclaw.agent.scorecard.v1",
            "agent_id": _slug(key),
            "preset": key,
            "dataset": dataset_name,
            "dataset_hash": dataset_hash,
            "symbols": sym_list,
            "bars": last_bars,
            "gates": {
                "confidence_threshold": cfg.get("confidence_threshold"),
                "volume_spike_min": cfg.get("volume_spike_min"),
                "regime_filter": cfg.get("regime") or None,
                "rsi_max": cfg.get("rsi_threshold"),
                "symbols": cfg.get("symbols"),
            },
            "unmodeled": unmodeled,
            "metrics": metrics,
            "engine": "runeclaw.backtest",
            "honest": True,
            "note": ("Design backtest on FROZEN benchmark data — percent/ratio "
                     "only, never a dollar figure. Re-run the identical backtest "
                     "in the Strategy Lab to reproduce."),
        }
        path = OUT_DIR / f"{card['agent_id']}.json"
        path.write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")
        written.append(str(path.relative_to(REPO)))
        m = card["metrics"]
        print(f"    -> {path.name}: ret {m.get('total_return_pct')}% "
              f"PF {m.get('profit_factor')} trades {m.get('total_trades')}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", default="data/benchmark/majors_1h")
    ap.add_argument("--symbols",
                    default="BTC/USDT:USDT,ETH/USDT:USDT,SOL/USDT:USDT")
    ap.add_argument("--last-bars", type=int, default=1500)
    args = ap.parse_args()
    print(f"Generating agent scorecards on {args.dataset} "
          f"({args.symbols}, {args.last_bars} bars)…")
    written = generate(args.dataset, args.symbols, args.last_bars)
    print(f"\nWrote {len(written)} scorecards:\n  " + "\n  ".join(written))


if __name__ == "__main__":
    main()
