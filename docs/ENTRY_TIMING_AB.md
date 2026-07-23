# Entry-timing gate — frozen-benchmark A/B

**Question.** `bot/core/entry_timing.py` turns a qualified signal into an *armed
setup* that fires only when the sub-degree confirms the turn (a confirmed
ATR-ZigZag pivot + a with-trade momentum candle), and disarms silently if the
idea's own stop is touched first or the validity window expires. It ships
**default-OFF** (`ENTRY_TIMING_ENABLED=false`, `ENTRY_TIMING_REGIMES=""`), with
the winning regime set "pending the frozen-benchmark A/B." This is that A/B.

**Method.** Backtest the frozen benchmarks with the path-dependent safety
throttles neutralised *identically* in both arms (live-performance governor and
symbol-loss-streak OFF; daily-loss and consecutive-loss limits relaxed;
`--breaker-reset-bars 24`) so the only variable is entry-timing. Metrics are the
per-regime P&L attribution the runner already emits (`entry_regime` bucket).

- **v1 (in-sample):** `ENTRY_TIMING_ENABLED=false` vs `true` (global) on
  `majors_1h` and `alts_1h`.
- **v2 (out-of-sample):** `ENTRY_TIMING_REGIMES=""` vs `TREND_DOWN` on
  `majors_1h_v2` and `alts_1h_v2` — testing the *actual proposed config*.

## Overall

| Benchmark | arm | Return | PF | Trades |
|---|---|---:|---:|---:|
| majors_1h (IS) | OFF | −6.67% | 0.77 | 313 |
| majors_1h (IS) | timing ON (global) | **+1.93%** | **1.35** | 293 |
| alts_1h (IS) | OFF | −3.14% | 0.87 | 508 |
| alts_1h (IS) | timing ON (global) | **+7.47%** | **7.88**¹ | 29¹ |
| majors_1h_v2 (OOS) | OFF | −6.72% | 0.64 | 265 |
| majors_1h_v2 (OOS) | TREND_DOWN | **−4.97%** | **0.91** | 954 |
| alts_1h_v2 (OOS) | OFF | −9.61% | 0.60 | 147 |
| alts_1h_v2 (OOS) | TREND_DOWN | **−2.92%** | **0.93** | 495 |

¹ global-on over-selects on alts (508→29 trades); high PF on a tiny sample.

## The signal: TREND_DOWN — improves in every dataset

| Dataset | TREND_DOWN PF: OFF → timing |
|---|---:|
| majors_1h (IS) | 0.86 → **1.73** |
| alts_1h (IS) | 1.01 → 2.55–88.86¹ |
| majors_1h_v2 (OOS) | 0.70 → **1.27** |
| alts_1h_v2 (OOS) | 0.66 → **2.55** |

Other regimes are neutral-to-negative and are **not** whitelisted:
- **TREND_UP** — no help (majors 0.40→0.16; alts 0.54→0.54).
- **RANGE** — timing *hurts* it (majors 1.63→0.39; it was already profitable
  without the gate).
- **EXPANSION** — flat / tiny-sample noise.

## Recommendation

Enable the gate **scoped to TREND_DOWN only**:

```
ENTRY_TIMING_ENABLED=false        # keep the global flag off
ENTRY_TIMING_REGIMES=TREND_DOWN   # active only in down-trends
```

This is the one regime where the gate reliably helps across both in-sample and
out-of-sample benchmarks. Do **not** enable it globally — that over-selects on
alts and hurts RANGE.

## Honest caveats

- The system is still net-negative overall on the v2 (OOS) benchmarks even with
  the gate — it *reduces the bleed*, it does not make the system profitable OOS.
- The OFF arms tripped path-dependent circuit-breakers (fewer trades), so exact
  magnitudes carry trade-selection drift; the TREND_DOWN *direction* is
  unambiguous across all four datasets.
- This is a live entry-behaviour change. It stays OFF by default; enabling it is
  an operator decision (set the env var above on the deployment).
