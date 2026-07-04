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

> **⚠️ Superseded — this table was single-exit, which live never runs.** The
> numbers above (mean OOS −0.90%, PF < 1) were produced with the backtest's
> *single full-exit* default (`BACKTEST_PARTIAL_TP=0`). The **live bot uses the
> partial-TP ladder** (`PARTIAL_TP_ENABLED` defaults True: bank 50% @1.5R →
> stop to breakeven, 30% @2.5R). Measuring the exit strategy live *actually
> runs* flips the result:
>
> | exit model | mean OOS | net | win | PF | profitable folds |
> |---|---:|---:|---:|---:|---:|
> | single-exit (old default) | −0.72%¹ | −$433 | 57% | 0.72 | 0/6 |
> | **partial-TP (= live)** | **+0.31%** | **+$188** | **64%** | **1.14** | **2/6** |
>
> ¹ with the #291 min-stop floor in place. **The bot is backtest-profitable on
> this window when measured honestly.** `--honest` now enables partial-TP by
> default so the benchmark reflects live; **superseded again below** — two more
> live-fidelity gaps (time-stop, fee model) were closed after this table.

## Current baseline: full live fidelity (time-stop + real fee)

Two more benchmark-vs-live gaps, found the same way as partial-TP: live runs a
**time-stop** (`TIME_STOP_ENABLED` defaults True — cuts a stale, non-profitable
position at its per-strategy horizon) that the backtest defaulted OFF
(`BACKTEST_TIME_STOP`), and the plain `--commission` default (0.1%) was stale
against the live risk engine's modeled taker rate (`CONFIG.risk.taker_fee_pct`,
**0.06%**). A 4-arm A/B on the frozen data (majors_1h, `--honest --walk-forward
6`) isolates each:

| time-stop | fee | mean OOS | net | win | PF |
|---|---:|---:|---:|---:|---:|
| off | 0.10% (old) | +0.31% | +$188 | 64% | 1.14 |
| **on** | 0.10% | +0.38% | +$230 | 63% | 1.18 |
| off | **0.06% (real)** | +0.42% | +$250 | 64% | 1.19 |
| **on** | **0.06%** | **+0.49%** | **+$294** | 63% | **1.24** |

Both fixes independently improve the result and stack cleanly. `--honest` now
enables the time-stop and uses the live-modeled fee by default (both still
override-able via `BACKTEST_TIME_STOP=0` / `--commission`).

**+0.49% OOS / PF 1.24 is the current baseline — every future A/B beats this
number, not the +0.31% one above.**

## Where the bleed is (pooled OOS attribution)

`--honest --walk-forward 6` pools **152 OOS trades: net −$540, win 55%, PF 0.67**
(`_pooled_attribution_report`). The single-run report is drawdown-locked to ~13
trades, so this pooled cut — fresh breaker/equity per fold — is the diagnostic one.

| Dimension | Bleeds | Earns |
|---|---|---|
| **Setup** | `swing` 120 tr, **−$534, PF 0.65** (≈99% of the loss) | `scalp` +$9 PF 1.24 · `intraday` −$15 PF 0.84 |
| **Signal type** | `volume_spike` 32 tr **PF 0.43** · `regime_trend` 74 tr −$280 PF 0.73 | `vwap_reversion` +$7 PF 1.26 (n=5) |
| **Regime** | `TREND_UP` PF 0.15 (29% win) · `TREND_DOWN` −$351 (n=87) · `RANGE` PF 0.56 | `EXPANSION` +$33 **PF 1.35** |
| **Trend align** | `with-trend` 100 tr −$495 PF 0.64 | — |

Two findings the frozen benchmark makes trustworthy:

1. **55% win rate but PF 0.67 → a reward/risk problem, not a hit-rate problem.**
   The bot is right more often than not; its losers were bigger than its winners.
   That pointed at the **exit engine** — and it was the answer: the live
   partial-TP ladder (bank early + stop-to-breakeven) lifts PF **0.72 → 1.14**
   and the return **−0.72% → +0.31%**. The single-exit backtest was measuring an
   exit strategy the bot never uses; most of the "bleed" was that artifact.
2. **Counter-trend fades are NOT the culprit — only 1 of 152 trades is
   counter-trend.** The audit's counter-trend hypothesis is *refuted* by the
   data; the loss lives in *with-trend* `swing` entries (late trend-following
   that gives back on the exit). This is exactly the false lead the frozen
   benchmark exists to catch before it becomes a wasted "fix."

Caveat: one macro window (Oct 2025 → Jul 2026, trend-down-heavy — 87/152 trades
were TREND_DOWN), so regime-specific numbers are window-dependent. The
reward/risk (exit) signal and the swing-setup concentration are the robust
takeaways; the next A/B targets those against this baseline.

## Attribution under the LIVE exit (partial-TP) — and a refuted gate

Re-run with the exit live actually uses (`BACKTEST_PARTIAL_TP=1`, now the
`--honest` default), the pooled **115-trade** picture is very different from the
single-exit table above — most families flip positive:

| Signal type | net | PF | | Setup | net | PF |
|---|---:|---:|---|---|---:|---:|
| `regime_trend` | +$215 | 1.25 | | `intraday` | +$181 | 5.19 |
| `vwap_reversion` | +$150 | ∞ | | `swing` | +$75 | 1.06 |
| `volume_spike` (n=7) | −$29 | 0.61 | | `scalp` (n=4) | −$67 | 0.00 |
| `momentum_confluence` | **−$147** | 0.62 | | | | |

**Refuted: gating `momentum_confluence`.** It's the one meaningful-sample
negative-edge family (34 tr, PF 0.62), so the obvious move is to skip it. A
controlled A/B says **don't** — skipping it drops the benchmark **+0.31% →
−0.26%** (net +$188 → −$153) and *raises* trade count 115 → 144. Per-family PnL
is **not additive** in a portfolio: freeing a family's position slots just lets
worse trades fill them. The `SKIP_SIGNAL_TYPES` lever exists (default empty, no
behavior change) but the benchmark says leave it empty on this universe. Recorded
so it isn't re-chased.

## Is live tracking the benchmark? (`bot.backtest.parity`)

The benchmark says the strategy is profitable here; the parity report closes the
loop against reality. It reads the LIVE realized trades and reports the same lens
— realized PF / win / net, **fee parity** (realized round-trip fee rate vs the
modeled `commission_pct`), and per-signal-type / per-setup / per-exit-reason
breakdowns — so a fills/fees/slippage gap between live and the +0.31% backtest
shows up directly:

```bash
python -m bot.backtest.parity          # reads data/closed_trades.json
python -m bot.backtest.parity --file <path.json>
```

Pure, read-only (no exchange calls). The point isn't to reproduce backtest P&L
trade-for-trade (live and backtest take different trades) but to answer: are live
*fills and fees* as good as the model assumes, and is live realized edge in the
same ballpark as the benchmark? A `fee_vs_model > 1.25×` or a realized PF well
under 1.14 is the signal that execution — not the strategy — is the leak.

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

## Second snapshot: the alt-coin universe (`data/benchmark/alts_1h/`)

The majors snapshot answers "is the strategy sound." It doesn't answer "should
the scanner be trading the symbols live actually holds" — TAG, BLESS, and
similar low-cap/newer-listing perps are a structurally different universe
(thinner books, wider spreads, younger listings) from BTC/ETH/SOL. A second
frozen snapshot, `alts_1h`, covers the universe live actually trades:

| | |
|---|---|
| Universe | 12 alt/meme perps (TAG BLESS HOME SYRUP TRUMP PENGU WIF PEPE FLOKI SEI APT ARB) |
| Bars | 5,994 × 1h per symbol |
| `dataset_hash` | `232d1946e469…` |
| Size | ~1 MB (gzipped CSV + `manifest.json`) |

```bash
python -m bot.backtest.runner --dataset data/benchmark/alts_1h --honest --walk-forward 6
```

**Result, full live fidelity (time-stop + real fee, same settings as the +0.49%
majors baseline): mean OOS −0.74%, 1/6 profitable folds, worst −4.51% (8.5%
maxDD). Pooled: 117 trades, net −$444, win 53%, PF 0.73.**

| Fold | Trades | OOS Return | Win | maxDD | PF |
|-----:|-------:|-----------:|----:|------:|----:|
| 0 | 27 | −4.51% | 48% | 8.50% | 0.42 |
| 1 | 12 | −0.27% | 33% | 0.91% | 0.85 |
| 2 | 30 | +1.01% | 67% | 0.93% | 1.57 |
| 3 | 8 | −0.00% | 38% | 0.85% | 1.00 |
| 4 | 29 | −0.04% | 62% | 2.38% | 0.99 |
| 5 | 11 | −0.63% | 36% | 0.85% | 0.54 |

Pooled attribution — **every** regime and setup bucket is negative:

| Dimension | Worst | Best |
|---|---|---|
| Regime | `TREND_DOWN` −$217 (n=70) · `TREND_UP` −$100 · `EXPANSION` −$87 | `RANGE` −$40 (least bad, still negative) |
| Setup | `swing` −$329 (n=94) · `intraday` −$87 · `scalp` −$29 | none positive |
| Signal | `regime_trend` −$259 (n=63) · `momentum_confluence` −$185 | `volume_spike` +$18 (n=12, thin) |

**Same strategy, same fidelity settings, same walk-forward — majors: +$294 / PF
1.24; alts: −$444 / PF 0.73.** This is not noise from a couple of bad trades: it
is negative across every regime, every setup, and every signal family bar one
thin bucket. The live TAG/BLESS losses are consistent with trading a
structurally negative-edge universe, not with a strategy bug.

**Implication (not yet acted on):** this is evidence for restricting the live
scanner/auto-trade universe away from this alt-coin class, or applying much
stricter gating specifically to it — a policy change, not a code bug, so it's
recorded here rather than shipped silently. `CONFIG.top_movers_count` and the
scanner's category allocation are the levers if this warrants a change.

## Refreshing the snapshot

Re-run step 1 to fetch a newer window (e.g. quarterly). This changes the
`dataset_hash`, so re-baseline before comparing across snapshots — a number from
one snapshot is not comparable to a number from another.
