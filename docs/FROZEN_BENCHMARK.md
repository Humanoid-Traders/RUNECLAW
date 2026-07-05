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
thin bucket. Standalone, alts looks like a structurally negative-edge universe —
**but see below: that conclusion is refuted once the two are combined.**

## Combined-universe A/B — the "restrict to majors" hypothesis is REFUTED

The natural next move from the alts result is "restrict the live scanner to
majors only." `--dataset` now accepts a **comma-separated list of snapshot
dirs**, merging them into one combined universe — built specifically to test
this:

```bash
python -m bot.backtest.runner --dataset data/benchmark/majors_1h,data/benchmark/alts_1h \
    --honest --walk-forward 6
```

**Result (22 symbols, `dataset_hash=062a1919310d…`): mean OOS +0.68%, 3/6
profitable folds, worst −1.95%. Pooled: 136 trades, net +$410, win 60%, PF
1.29 — BEATS majors-only (+0.49% / PF 1.24), not just alts-only.**

| Universe | Mean OOS | Net | Win | PF | Profitable folds |
|---|---:|---:|---:|---:|---:|
| Majors only (10 symbols) | +0.49% | +$294 | 63% | 1.24 | 2/6 |
| Alts only (12 symbols) | −0.74% | −$444 | 53% | 0.73 | 1/6 |
| **Combined (22 symbols)** | **+0.68%** | **+$410** | 60% | **1.29** | 3/6 |

And within the *same* combined run, the per-universe roles **flip** from their
standalone results — majors become the weaker side, alts the stronger:

| | Alone | In the combined mix |
|---|---:|---:|
| Majors | +$294 / PF 1.24 | **−$136 / PF 0.84** |
| Alts | −$444 / PF 0.73 | **+$493 / PF 1.86** |

**This is not noise — it's the same lesson as the refuted `SKIP_SIGNAL_TYPES`
gate (#293): per-symbol/per-family attribution is not additive in a
portfolio.** The shared risk engine (position-count caps, correlation/VaR
limits, the daily-loss breaker) means which trades actually clear and how
they're sized depends on the *entire* universe in play, not on each symbol in
isolation. A losing streak that would halt trading in an alts-only run gets
diluted by majors' steadier signal flow in the combined book (and vice versa),
changing the realized trade set on both sides.

**Conclusion: do NOT restrict the live universe to majors-only** — that
"obvious" fix would, by this evidence, make things *worse*. The standalone
alts number is real but doesn't predict the mixed-universe outcome; only the
combined backtest does, and it favors the status quo (scan both). Recorded
here so this refuted hypothesis isn't re-chased; `bot/backtest/snapshot.py`'s
`load_manifest_multi`/`load_symbol_multi`/`load_dataset_multi` make any future
combined-universe question a one-line `--dataset a,b` away instead of an
ad-hoc script.

## Fee-churn reduction — raising the confidence floor (0.55 -> 0.60, ENABLED)

`--confidence-threshold` (new `_run_portfolio` CLI flag, layered on top of
whatever the risk engine's own `CONFIDENCE` check already enforces via
`CONFIG.risk.min_confidence`) was swept against the combined 22-symbol
universe and the majors-only universe to see if raising the entry bar cuts
low-conviction trades (and their commission drag) without hurting expectancy:

```bash
python -m bot.backtest.runner --dataset data/benchmark/majors_1h,data/benchmark/alts_1h \
    --honest --walk-forward 6 --confidence-threshold 0.6
```

| Extra floor | Combined mean OOS | Combined PF | Combined trades/net | Majors mean OOS | Majors PF | Majors trades/net |
|---|---:|---:|---:|---:|---:|---:|
| 0.0 (none) | +0.68% | 1.29 | 136 / +$410 | +0.49% | 1.24 | 117 / +$294 |
| 0.55 | +0.68% | 1.29 | 136 / +$410 | — | — | — |
| **0.60** | **+1.00%** | **1.55** | **118 / +$599** | **+0.62%** | **1.38** | **105 / +$371** |
| 0.65 | +0.78% | 1.44 | 128 / +$468 | — | — | — |

**0.55 is a byte-for-byte no-op** — the live risk engine already enforces
`MIN_CONFIDENCE=0.55` on the exact same `idea.confidence` value (the backtest
engine runs the same `RiskEngine` check), so an *extra* gate at 0.55 rejects
nothing beyond what already gets rejected. This confirms the baseline runs
above were never "ungated" — they were already filtering at 0.55.

**0.60 is a genuine, robust win on both universes** — fewer, higher-conviction
trades (down ~10-13%) with meaningfully better PF and total return on both the
majors-only and combined-universe benchmarks. 0.65 also beats the 0.0/0.55
baseline but underperforms 0.60, so 0.60 is the sweep's local optimum, not an
edge-of-range artifact.

**Enabled**: `RiskLimits.min_confidence` default raised from 0.55 to 0.60 in
`bot/config.py` (env override `MIN_CONFIDENCE` unchanged) — this is the actual
gate the finding was measured against, so raising the default is how the
result reaches live trading, not just the backtest CLI knob used to discover
it.

## Regime-aware sizing: TREND_UP down-size (0.7x, ENABLED) + a cap-tightening bug fix

Round 2's last item targeted the regime-conditional position-size multiplier
(`RiskEngine._REGIME_MULTIPLIERS`, default ON) — attribution across this
session's sweeps repeatedly showed TREND_UP as the weakest/most inconsistent
regime bucket (majors-only PF as low as 0.22-0.29 vs TREND_DOWN's reliable
1.25-1.74), unlike every other regime. Added `TREND_UP_SIZE_MULT` (default
matched the existing 1.2x boost) to A/B it independently of the static table.

**First sweep found nothing — because the multiplier was already a no-op.**
1.2/1.0/0.8/0.7/0.5 all produced byte-identical results. Root cause: the
fixed-fractional pre-cap `position_usd` routinely exceeds the notional cap by
a wide margin ("binds on ~every crypto trade" — same reason `vol_target_sizing`
exists), so multiplying an already-oversized value by anything in the 0.5-1.5x
range still gets clamped to the exact same `max_notional_usd` — the regime
multiplier had no effect on the actual executed size for ANY regime, boost or
reduce, live or backtest, since this has always been the sizing order.

**Fix**: TREND_UP-scoped only (not CHOP/RANGE, to avoid silently changing
already-shipped, already-measured behavior for regimes this A/B didn't test) —
`TREND_UP_SIZE_MULT<1.0` now also tightens `max_notional_usd` itself, mirroring
`vol_target_sizing`'s tighten-only pattern. `>=1.0` (a boost) still only
affects the uncapped pre-cap value, per the original C2-29 fix (a boost must
never let a trade exceed the hard cap).

With the fix in place, re-swept against both frozen benchmarks:

| TREND_UP_SIZE_MULT | Combined mean OOS | Combined PF | Combined trades/net | Majors mean OOS | Majors PF | Majors trades/net |
|---|---:|---:|---:|---:|---:|---:|
| 1.2 (old default) | +1.00% | 1.55 | 118 / +$599 | +0.62% | 1.38 | 105 / +$371 |
| 0.8 | +0.94% | 1.53 | 118 / +$566 | — | — | — |
| **0.7** | **+1.12%** | **1.67** | **126 / +$673** | **+0.62%** | **1.39** | **104 / +$370** |
| 0.5 | +1.01% | 1.60 | 126 / +$605 | — | — | — |
| 0.3 | +0.90% | 1.53 | 126 / +$538 | — | — | — |

0.75-0.79 tested slightly higher on the combined universe (+1.15% at 0.75) but
sits on a sharp, noisy trade-count discontinuity (0.77 fell back to the
118-trade bucket while 0.75/0.78 landed in the 126-trade bucket) — a hallmark
of overfitting to this single window rather than a real edge, so **0.7** was
picked: solidly inside the improved basin on both sides, not chasing the peak.

**Enabled**: `RiskLimits.trend_up_size_mult` default 0.7. Combined universe is
a clear win (+12% relative return, PF +0.12, 8 more trades at smaller size
each); majors-only is a wash on return but strictly better on PF and worst-
fold drawdown (-1.06% vs -1.22%) — no universe got worse, so shipped.

## Extending the cap-tightening fix to CHOP/RANGE

The TREND_UP cap-tightening fix above was deliberately scoped narrowly (only
TREND_UP) to avoid silently changing CHOP (0.5x) and RANGE (0.7x)'s already-
shipped, already-measured behavior in the same PR. Round 3 revisited that
scope: since ANY sub-1.0 regime multiplier was equally a no-op (not just
TREND_UP's), CHOP and RANGE's reductions had also never actually applied in
either live or backtest. Generalized the fix (`if regime_mult < 1.0:` with no
regime-name gate) and re-swept both frozen benchmarks:

| | Before (TREND_UP-only) | After (all reduce-regimes) |
|---|---:|---:|
| Combined mean OOS / PF / trades | +1.12% / 1.67 / 126 | +1.13% / 1.67 / 126 |
| Majors mean OOS / PF / worst fold | +0.62% / 1.39 / -1.06% | **+0.67% / 1.43 / -0.85%** |

Combined universe barely moved (CHOP/RANGE trades are a small slice of that
mix); majors-only improved meaningfully on both return and worst-case
drawdown. No universe got worse, so the general fix ships — `regime_mult<1.0`
now tightens the cap regardless of which regime it came from.

## Trailing-stop tuning — explored, no actionable edge found

Round 3's second item swept the trailing-stop's parameters against the
combined frozen benchmark. Two things worth recording so they aren't
re-chased:

**The multi-stage ATR knobs are dead code under `--honest`.** The first
sweep attempt (`TRAIL_STAGE{1,2,3}_ATR_MULT`, the per-stage trail distance in
`bot/utils/trailing.py`'s `update_trailing_stop`) produced byte-identical
results across 4 very different configurations. Root cause: `--honest` sets
`BACKTEST_PARTIAL_TP=1`, and once a position has a `ptp_state`,
`bot/backtest/engine.py:492-498` routes it entirely through
`_check_ladder_intrabar` — the multi-stage trail (`update_trailing_stop`) is
never reached for any position. This mirrors live, where `PARTIAL_TP_ENABLED`
is also default True, so the ladder is the actually-operative exit mechanism
in both places — not a fidelity mismatch, just a discoverability gap in which
knob is real. The runner (final 20% after both TP legs bank profit) is
trailed by a *different* parameter: `PARTIAL_RUNNER_TRAIL_ATR`
(`bot/config.py`, default `0.8`).

**`PARTIAL_RUNNER_TRAIL_ATR` itself has negligible sensitivity.** Swept
0.5/0.6/0.8/1.0/1.2/1.5 on the combined majors+alts benchmark
(`--honest --walk-forward 6 --confidence-threshold 0.6`):

| Value | Mean OOS | PF | Trades | Net |
|---|---:|---:|---:|---:|
| 0.5 | +1.13% | 1.67 | 126 | +$677.00 |
| 0.6 | +1.13% | 1.67 | 126 | +$676.91 |
| 0.8 (default) | +1.13% | 1.67 | 126 | +$676.74 |
| 1.0 | +1.13% | 1.67 | 126 | +$676.41 |
| 1.2 | +1.13% | 1.67 | 126 | +$675.45 |
| 1.5 | +1.12% | 1.67 | 126 | +$672.97 |

Same trade count and PF at every value; net PnL varies by <1% across the
whole 0.5-1.5 range. Makes sense given the runner is only the last 20% of a
position after both partial-TP legs have already banked profit — its exact
trail distance just doesn't move the portfolio needle much either way. No
value beats the default, so nothing to ship — keeping `PARTIAL_RUNNER_TRAIL_ATR=0.8`.

## Per-strategy-type confidence floor — REFUTED

Round 3's third item tested whether letting the risk engine use
`StrategyTypeConfig`'s existing per-type confidence floors (scalp 0.65 /
intraday 0.55 / swing 0.50 / position 0.45 — already enforced by the analyzer
at idea-generation time) instead of the flat global `min_confidence` (0.60,
shipped in round 2 item 3) would beat the flat floor.

**Methodology note**: the first sweep attempt included the backtest's extra
`--confidence-threshold 0.6` CLI flag out of habit (carried over from earlier
round-3 items) — this flag is a *separate*, still-flat 0.6 gate applied before
the idea even reaches the risk engine, so it silently masked the per-type
floors and made the "on" run byte-identical to "off". Removed the flag (which
is genuinely redundant with the now-0.60 global default when the per-strategy
flag is off, but not when it's on) for a valid test.

**Result: clearly refuted on both universes** — three of four per-type floors
(swing 0.50, intraday 0.55, position 0.45) sit below the proven 0.60 global
default, so enabling this reopens exactly the low-conviction trades that
round 2 item 3's bump was designed to filter out:

| | Combined OFF | Combined ON | Majors OFF | Majors ON |
|---|---:|---:|---:|---:|
| Mean OOS | +1.13% | +0.40% | +0.67% | +0.42% |
| PF | 1.67 | 1.17 | 1.43 | 1.20 |
| Trades | 126 | 142 | 104 | 125 |
| Worst fold | -1.54% | -1.93% | -0.85% | -1.06% |

Worse return, worse PF, more (weaker) trades, worse worst-case drawdown —
consistently, on both universes. **Not shipped**: `per_strategy_confidence_
floor_enabled` stays default OFF (the lever exists, gated, for anyone who
wants to re-test it against a future snapshot, but should not be flipped on
based on this evidence).

## Refreshing the snapshot

Re-run step 1 to fetch a newer window (e.g. quarterly). This changes the
`dataset_hash`, so re-baseline before comparing across snapshots — a number from
one snapshot is not comparable to a number from another.
