"""Fixed cached benchmark dataset — fetch the A/B universe ONCE, then freeze it.

The honest benchmark (``runner --symbols ... --honest --walk-forward 6``) fetches
~6000 FRESH bars per symbol at run time, anchored to the exchange clock. Two runs
minutes apart therefore see DIFFERENT data windows, and ordinary run-to-run
variance (~0.5pp of return) swamps the small effects most signal/money A/Bs try to
measure. You cannot attribute a delta to a code change when the data underneath it
moved between the two runs.

This module freezes the universe into a versioned on-disk snapshot so every A/B
arm reads byte-identical candles:

  * ``snapshot_dataset(symbols, timeframe, limit, out_dir)`` fetches once and
    writes ``<out_dir>/<safe_symbol>.csv.gz`` plus a ``manifest.json`` recording,
    per symbol, the bar count / first+last timestamp / content sha256, and an
    overall ``dataset_hash`` over the whole universe.
  * ``load_symbol`` / ``load_dataset`` read them back; ``verify_dataset``
    recomputes every hash and flags any drift.
  * The runner's ``--dataset DIR`` flag routes every fetch through ``load_symbol``
    instead of the live exchange, and stamps the ``dataset_hash`` into the saved
    result so a run is self-describing about *which* frozen data it measured.

Committed under ``data/benchmark/``, the snapshot survives container rebuilds, so a
fresh cloud sandbox runs the IDENTICAL benchmark instead of silently re-fetching a
new window and quietly changing the answer.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime
from bot.compat import UTC
from pathlib import Path

from bot.backtest.data_loader import DataLoader
from bot.backtest.models import BacktestBar

MANIFEST_NAME = "manifest.json"
SNAPSHOT_VERSION = 1

# The canonical honest-benchmark universe: the 10 liquid majors the A/B harness
# runs on, as Bitget USDT-M perps. Keeping this here means `python -m
# bot.backtest.snapshot` with no --symbols reproduces the exact benchmark set.
DEFAULT_BENCHMARK_SYMBOLS = [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT",
    "DOGE/USDT:USDT", "ADA/USDT:USDT", "LINK/USDT:USDT", "AVAX/USDT:USDT",
    "LTC/USDT:USDT", "BNB/USDT:USDT",
]
DEFAULT_BENCHMARK_DIR = "data/benchmark/majors_1h"


def safe_symbol(symbol: str) -> str:
    """Filesystem-safe stem for a symbol: ``BTC/USDT:USDT`` -> ``BTC_USDT_USDT``."""
    return symbol.replace("/", "_").replace(":", "_")


def dataset_hash(per_symbol_hashes: dict[str, str]) -> str:
    """Deterministic hash over the whole universe: sha256 of the sorted
    ``symbol=content_hash`` lines. Independent of dict order and of file
    layout, so it changes iff some symbol's candles changed."""
    h = hashlib.sha256()
    for sym in sorted(per_symbol_hashes):
        h.update(f"{sym}={per_symbol_hashes[sym]}\n".encode())
    return h.hexdigest()


def _manifest_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / MANIFEST_NAME


def build_manifest(
    out_dir: str | Path,
    timeframe: str,
    limit: int,
    per_symbol: dict[str, list[BacktestBar]],
    *,
    created_at: datetime | None = None,
) -> dict:
    """Assemble (but do not write) the manifest for a set of fetched bars."""
    entries: dict[str, dict] = {}
    hashes: dict[str, str] = {}
    for sym, bars in per_symbol.items():
        chash = DataLoader.content_hash(bars)
        hashes[sym] = chash
        srt = sorted(bars, key=lambda b: b.timestamp)
        entries[sym] = {
            "file": f"{safe_symbol(sym)}.csv.gz",
            "bars": len(bars),
            "first": srt[0].timestamp.isoformat() if srt else None,
            "last": srt[-1].timestamp.isoformat() if srt else None,
            "sha256": chash,
        }
    return {
        "version": SNAPSHOT_VERSION,
        "timeframe": timeframe,
        "limit": limit,
        "created_at": (created_at or datetime.now(UTC)).isoformat(),
        "dataset_hash": dataset_hash(hashes),
        "symbols": dict(sorted(entries.items())),
    }


async def snapshot_dataset(
    symbols: list[str],
    timeframe: str,
    limit: int,
    out_dir: str | Path,
    *,
    min_bars: int = 220,
    created_at: datetime | None = None,
) -> dict:
    """Fetch each symbol once from Bitget and freeze it under ``out_dir``.

    Writes one gzipped CSV per symbol plus ``manifest.json``. A symbol that
    returns fewer than ``min_bars`` is skipped with a warning rather than
    poisoning the dataset with a stub. Returns the written manifest.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    per_symbol: dict[str, list[BacktestBar]] = {}
    for sym in symbols:
        try:
            bars = await DataLoader.from_bitget(symbol=sym, timeframe=timeframe, limit=limit)
        except Exception as exc:  # noqa: BLE001 — network/venue errors are per-symbol
            print(f"  ✗ {sym}: fetch failed ({exc}) — skipped")
            continue
        if len(bars) < min_bars:
            print(f"  ✗ {sym}: only {len(bars)} bars (< {min_bars}) — skipped")
            continue
        path = out / f"{safe_symbol(sym)}.csv.gz"
        DataLoader.save_csv(bars, str(path))
        per_symbol[sym] = bars
        print(f"  ✓ {sym}: {len(bars)} bars -> {path.name}")

    if not per_symbol:
        raise RuntimeError("no symbols fetched — refusing to write an empty snapshot")

    manifest = build_manifest(out, timeframe, limit, per_symbol, created_at=created_at)
    _manifest_path(out).write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"  manifest: {len(per_symbol)} symbols, dataset_hash={manifest['dataset_hash'][:12]}…")
    return manifest


def load_manifest(out_dir: str | Path) -> dict:
    """Read the snapshot manifest. Raises FileNotFoundError if the dir is not
    a snapshot (so a mistyped --dataset path fails loudly, not silently)."""
    p = _manifest_path(out_dir)
    if not p.exists():
        raise FileNotFoundError(f"no {MANIFEST_NAME} in {out_dir} — not a benchmark snapshot")
    man = json.loads(p.read_text())
    if not isinstance(man, dict):
        raise ValueError(f"malformed manifest in {out_dir}: expected a JSON object")
    return man


def load_symbol(out_dir: str | Path, symbol: str, manifest: dict | None = None) -> list[BacktestBar]:
    """Load one symbol's frozen bars. Raises KeyError if the symbol is not in
    the snapshot — a --dataset run must never silently fall back to live data
    for a symbol it does not have."""
    man = manifest or load_manifest(out_dir)
    entry = man.get("symbols", {}).get(symbol)
    if entry is None:
        raise KeyError(
            f"symbol {symbol!r} not in snapshot {out_dir} "
            f"(have: {', '.join(sorted(man.get('symbols', {})))})"
        )
    bars = DataLoader.from_csv(str(Path(out_dir) / entry["file"]))
    for b in bars:
        b.symbol = symbol
    return bars


def load_dataset(out_dir: str | Path) -> dict[str, list[BacktestBar]]:
    """Load every symbol in the snapshot, keyed by symbol."""
    man = load_manifest(out_dir)
    return {sym: load_symbol(out_dir, sym, man) for sym in man.get("symbols", {})}


def verify_dataset(out_dir: str | Path) -> tuple[bool, list[str]]:
    """Recompute every content hash and the dataset hash from the files on disk
    and compare to the manifest. Returns ``(ok, problems)``. This is the
    integrity gate: it proves the committed candles are exactly what the
    manifest claims, so an A/B can trust the ``dataset_hash`` it stamps."""
    problems: list[str] = []
    try:
        man = load_manifest(out_dir)
    except FileNotFoundError as exc:
        return False, [str(exc)]

    recomputed: dict[str, str] = {}
    for sym, entry in man.get("symbols", {}).items():
        fpath = Path(out_dir) / entry["file"]
        if not fpath.exists():
            problems.append(f"{sym}: file {entry['file']} missing")
            continue
        bars = DataLoader.from_csv(str(fpath))
        chash = DataLoader.content_hash(bars)
        recomputed[sym] = chash
        if len(bars) != entry.get("bars"):
            problems.append(f"{sym}: bar count {len(bars)} != manifest {entry.get('bars')}")
        if chash != entry.get("sha256"):
            problems.append(f"{sym}: content hash drifted from manifest")

    if recomputed:
        recomputed_ds = dataset_hash(recomputed)
        if recomputed_ds != man.get("dataset_hash"):
            problems.append("dataset_hash drifted from manifest")
    return (not problems), problems


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze a benchmark OHLCV dataset for reproducible A/B testing.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Freeze the canonical honest-benchmark universe (10 majors, 6000x 1h bars):
  python -m bot.backtest.snapshot --limit 6000 --out data/benchmark/majors_1h

  # Verify a committed snapshot's integrity (no fetch):
  python -m bot.backtest.snapshot --verify --out data/benchmark/majors_1h

  # Then run any A/B against it — both arms read byte-identical data:
  python -m bot.backtest.runner --dataset data/benchmark/majors_1h \\
      --symbols BTC/USDT:USDT,ETH/USDT:USDT --honest --walk-forward 6
        """,
    )
    parser.add_argument("--symbols", type=str, default="",
                        help="Comma-separated symbols (default: the 10 benchmark majors)")
    parser.add_argument("--timeframe", type=str, default="1h", help="Candle timeframe (default: 1h)")
    parser.add_argument("--limit", type=int, default=6000, help="Bars per symbol (default: 6000)")
    parser.add_argument("--out", type=str, default=DEFAULT_BENCHMARK_DIR,
                        help=f"Output directory (default: {DEFAULT_BENCHMARK_DIR})")
    parser.add_argument("--min-bars", type=int, default=220,
                        help="Skip a symbol returning fewer than this many bars (default: 220)")
    parser.add_argument("--verify", action="store_true",
                        help="Verify an existing snapshot's integrity instead of fetching")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.verify:
        ok, problems = verify_dataset(args.out)
        if ok:
            man = load_manifest(args.out)
            print(f"  ✓ snapshot OK: {len(man.get('symbols', {}))} symbols, "
                  f"dataset_hash={man.get('dataset_hash', '')[:12]}…")
            sys.exit(0)
        print("  ✗ snapshot FAILED verification:")
        for p in problems:
            print(f"      - {p}")
        sys.exit(1)

    symbols = ([s.strip() for s in args.symbols.split(",") if s.strip()]
               or list(DEFAULT_BENCHMARK_SYMBOLS))
    print(f"\n  Freezing {len(symbols)} symbols x {args.limit} {args.timeframe} "
          f"bars -> {args.out}")
    asyncio.run(snapshot_dataset(
        symbols, args.timeframe, args.limit, args.out, min_bars=args.min_bars))


if __name__ == "__main__":
    main()
