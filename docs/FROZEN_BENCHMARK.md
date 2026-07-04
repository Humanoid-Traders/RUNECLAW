# Frozen Benchmark Dataset — trustworthy A/B testing

**Added:** 2026-07-04 · **Module:** `bot/backtest/snapshot.py` · **Data:** `data/benchmark/majors_1h/`

## The problem it fixes

The honest benchmark

```bash
python -m bot.backtest.runner --symbols BTC/USDT:USDT,ETH/USDT:USDT,… \
    --limit 6000 --timeframe 1h --honest --walk-forward 6
```

fetches **~6000 fresh bars per symbol at run time**, anchored to the exchange
clock (`exchange.milliseconds()`). Two runs a few minutes apart therefore measure
**different data windows**, and ordinary run-to-run variance (~0.5pp of return)
**swamps the small effects** most signal/money A/Bs are trying to detect. You
cannot attribute a delta to a code change when the data underneath it moved
between the two runs — this is exactly how a partial-TP "+0.15% breakthrough" was
once chased before a controlled test showed it was pure data-window drift.

## The fix

Freeze the universe **once** into a committed, content-hashed snapshot. Every A/B
arm then reads byte-identical candles, so any delta is attributable to the code.

```bash
# 1. Freeze the canonical universe (already committed; re-run only to refresh):
python -m bot.backtest.snapshot --limit 6000 --out data/benchmark/majors_1h

# 2. Verify a committed snapshot's integrity (no network):
python -m bot.backtest.snapshot --verify --out data/benchmark/majors_1h

# 3. Run ANY A/B against it — both arms read identical bars:
python -m bot.backtest.runner --dataset data/benchmark/majors_1h \
    --honest --walk-forward 6
```

With `--dataset DIR` and no `--symbols`, the snapshot's full universe runs as a
portfolio — the one-liner benchmark above. The run stamps
`data_source=frozen_snapshot:<dataset_hash>` so every result is self-describing
about *which* frozen data it measured.

### Why committed?

The cloud execution environment is ephemeral — containers are reclaimed and the
repo is re-cloned fresh. If the snapshot lived only in `/tmp`, a new container
would fall back to live fetches and silently reintroduce the variance. Committing
it (1.1 MB gzipped for 10 symbols × ~6000 bars) means a fresh sandbox runs the
*identical* benchmark.

## The current snapshot

| | |
|---|---|
| Universe | 10 majors as USDT-M perps (BTC ETH SOL XRP DOGE ADA LINK AVAX LTC BNB) |
| Bars | 5,994 × 1h per symbol (~250 days) |
| Window | 2025-10-27 → 2026-07-04 |
| `dataset_hash` | `8dbe73514ce8…` |
| Size | 1.1 MB (gzipped CSV + `manifest.json`) |

## Honest baseline (this snapshot, `--honest --walk-forward 6`)

| Fold | Trades | OOS Return | Win | maxDD | PF |
|-----:|-------:|-----------:|----:|------:|----:|
| 0 | 45 | −0.38% | 71% | 7.32% | 0.95 |
| 1 | 32 | −1.28% | 53% | 1.50% | 0.45 |
| 2 | 19 | −0.47% | 53% | 1.23% | 0.71 |
| 3 | 17 | −1.04% | 35% | 1.35% | 0.28 |
| 4 | 26 | −1.29% | 50% | 1.33% | 0.34 |
| 5 | 13 | −0.94% | 46% | 1.22% | 0.25 |

**Mean OOS −0.90%, 0/6 folds profitable, PF < 1 throughout.** This is the honest,
reproducible baseline the edge work must beat. It is consistent with the audit's
diagnosis (fee churn + counter-trend fades dragging PF below 1). **Every future
signal/money A/B is measured as a delta against this number, on this data.**

## Integrity guarantees (locked by `tests/test_benchmark_snapshot.py`)

- gzip CSV round-trips preserve the exact candles (`DataLoader.content_hash`
  stable across plain ⇄ gzip);
- re-snapshotting identical candles produces byte-identical files (gzip `mtime=0`)
  — clean git diffs, no header churn;
- the manifest `dataset_hash` is order-independent and drift-sensitive;
- `verify_dataset` recomputes every hash from disk and flags tampering or a
  missing file;
- a symbol missing from the snapshot is a **hard error**, never a silent live
  fallback that would quietly change the universe;
- and the keystone proof: **two portfolio backtests on the same frozen dataset
  yield byte-identical P&L** (`test_frozen_dataset_backtest_is_deterministic`) —
  the only run-to-run differences are wall-clock metadata (`duration_seconds`,
  the result `timestamp`, and each trade's random id), none of which affect an
  A/B comparison.

## Refreshing the snapshot

Re-run step 1 to fetch a newer window (e.g. quarterly). This changes the
`dataset_hash`, so re-baseline before comparing across snapshots — a number from
one snapshot is not comparable to a number from another.
