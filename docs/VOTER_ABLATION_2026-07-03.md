# Voter Ablation — 2026-07-03

First per-voter attribution run (`scripts/voter_ablation.py`) on the honest
portfolio benchmark: 10 Bitget USDT-M perps, 4h/1h, `--honest` (strict data +
next-open fills). Baseline run, then one run per voter with that voter's weight
zeroed (`ABLATE_VOTERS=<name>`), measuring the delta.

**Reading:** a voter whose removal *hurts* return carries edge (keep it); a
voter whose removal *helps* is harmful on this window (candidate to dark); a
~flat delta is dead weight.

## Baseline

`+0.39% return | PF 1.17 | 36 trades` — a **recent, choppy window** (single
`--fetch` of the latest ~6k bars; Sharpe was negative). This is *not* the
+2.15% / PF 1.96 numbers-of-record window; it's the last several months, which
have been unfavourable to the strategy. All deltas below describe edge **on
recent data**, one arm at a time (not additive).

## Ranked result (removal Δ vs baseline)

### Harmful on this window — removal *improves* return **and** profit factor
| voter | return | Δpp | PF | note |
|---|---|---|---|---|
| harmonic | +1.24% | +0.85 | 1.47 | removing ~triples return |
| chart_patterns | +1.13% | +0.74 | 1.61 | |
| candlestick | +0.99% | +0.60 | 1.70 | highest PF of any arm |
| poc_magnet | +0.90% | +0.51 | 1.28 | |
| reversal | +0.70% | +0.31 | 1.42 | |

### ~Neutral / dead weight (|Δ| < 0.2pp)
`wyckoff (+0.15)`, `macd (+0.12)`, `stochastic / mfi / keltner / bollinger
(+0.08, identical → rarely decisive)`, `sentiment (−0.07)`, `adx (−0.12)`,
`donchian (−0.14)`, `rsi (−0.15)`.

### Edge carriers — removal *craters* return (keep)
| voter | return | Δpp | PF |
|---|---|---|---|
| supply_demand | −1.71% | −2.10 | 0.57 |
| vwap | −1.69% | −2.08 | 0.59 |
| fibonacci | −1.56% | −1.95 | 0.59 |
| of_cvd_trend | −1.56% | −1.95 | 0.59 |
| vwap_bands | −1.56% | −1.95 | 0.59 |
| mtf_structure | −1.43% | −1.82 | 0.63 |
| taker | −1.43% | −1.82 | 0.63 |
| divergence | −1.15% | −1.54 | 0.66 |
| liquidity_sweep | −1.12% | −1.51 | 0.69 |
| mtf_bos | −0.95% | −1.34 | 0.75 |
| ema_ribbon | −0.89% | −1.28 | 0.51 |
| volume_spike | −0.61% | −1.00 | 0.80 |
| volume_profile | −0.59% | −0.98 | 0.85 |
| obv | −0.28 | | mild |

## Interpretation

A clean thematic split:

- **The smart-money / structure / order-flow voters carry the edge** —
  supply/demand, VWAP + bands, fib, CVD/taker order flow, MTF structure,
  liquidity sweeps. Zeroing any *one* flips the book strongly negative.
- **The subjective pattern voters look harmful on recent data** — harmonic,
  chart patterns, candlesticks, POC magnet, reversal. Removing each individually
  lifts both return and profit factor, several to PF > 1.4.

## What this is NOT (caveats before acting)

1. **One window, small sample.** ~22–42 trades per arm on a choppy, negative-
   Sharpe stretch. Suggestive, not proven.
2. **One-at-a-time ≠ combined.** The deltas are marginal, not additive — darking
   all five "harmful" voters together is a *separate* experiment, not the sum of
   five rows.
3. **Regime-blind.** Pattern voters may be harmful in chop yet useful in trend.
   The per-regime attribution (shipped in #259) must confirm the harm isn't
   window-specific.

## Recommended next experiment (measurement-first)

1. Combined run: `ABLATE_VOTERS=harmonic,chart_patterns,candlestick,poc_magnet,reversal`
   vs baseline, on the honest suite **and** the 6-fold walk-forward.
2. Per-regime P&L split of those five voters (regime attribution report).
3. **Only if** the harm persists out-of-sample and across regimes: dark them via
   a flag (default = current), ship, and A/B on the honest benchmark.

No voter weights are changed by this run — it is measurement only.

## Validation result — the lead did NOT survive walk-forward (VERDICT: do not ship)

Combined mute of all five "harmful" voters
(`ABLATE_VOTERS=harmonic,chart_patterns,candlestick,poc_magnet,reversal`) vs
baseline, on a fresh fetch, both in-sample and on the 6-fold walk-forward:

| | in-sample return | in-sample PF | in-sample Sharpe | win% |
|---|---|---|---|---|
| baseline | +1.24% | 1.47 | −0.87 | 71% |
| 5 muted | **+1.63%** | **1.78** | −0.73 | 75% |

| | WF profitable folds | WF mean OOS | WF worst |
|---|---|---|---|
| baseline | **1/6** | **−1.04%** | −1.98% |
| 5 muted | 0/6 | −1.22% | −1.56% |

**In-sample the mute looks like a clear win; out-of-sample it is WORSE** (0/6
folds vs 1/6, mean OOS −1.22% vs −1.04%). The single-window ablation "edge" was
**window-fitting** — the walk-forward, the actual generalization test, refutes
it.

**Decision: keep all five voters. No weights darked.** The ablation was a valid
lead-generator; the OOS check did its job and killed the lead before it could
overfit the live strategy. (Note both arms are negative OOS — consistent with
the strategy's long-standing weak walk-forward; removing voters does not fix
that.) Baseline also drifted +0.39% → +1.24% between the sweep and this run on
the same nominal window — the fetch-window sensitivity that is exactly why
walk-forward, not any single fetch, is the arbiter.
