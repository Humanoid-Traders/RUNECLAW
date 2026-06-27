# Full-Universe Deep Backtest

**Date:** 2026-06-27
**Harness:** `run_deep_backtest_full.py`
**Scope:** the entire 67-symbol scan universe (`bot/skills/scan_skill.py`) ×
5 market regimes × 5 seeds = **1,675 runs**, 1,500 1H bars each (~62 days/run).
**Runtime:** 2,070 s across 3 worker processes (1.24 s/run), per-bar audit logging
suppressed for speed.

> **Read with caveats.** The backtest runs on *synthetic* data, and two documented
> validity bugs still apply (see `docs/AUDIT_REPORT_V6.1.md`): **BT-H1** — the
> commission knob is non-functional (charges 0.06%, not the reported 0.1%), and
> **BT-H2** — session sizing uses wall-clock time, so runs are not bit-reproducible.
> These results demonstrate that the pipeline runs end-to-end and the risk gates
> fire across the whole universe — **not** live trading edge.

## Headline

| Metric | Value |
|--------|-------|
| Valid runs | **1,675 / 1,675 (0 errors)** |
| Total trades | 4,727 |
| Profitable runs | 786 / 1,675 (46.9%) |
| Symbols with >0 avg return | 55 / 67 (median per-symbol +0.66%) |
| Avg return | **+1.13%** (best +18.20%, worst −1.58%) |
| Avg max drawdown | 0.61% (worst 2.39%) |
| Crashed runs (DD>20%) | **0** |
| Avg win rate | 42.1% |
| Avg Sharpe / Sortino | +0.35 / +0.83 |
| Avg profit factor | 142.6 *(metric artifact — see below)* |

**Zero errors across all 67 symbols** validates the BT-CRASH-1/2 fixes at scale —
the memecoins/low-priced assets (BONK, PEPE, PUMP, FARTCOIN, …) that previously
crashed the 20-symbol run now degrade gracefully.

## By regime

| Regime | Runs | Avg Ret% | Avg DD% | WR% | Sharpe | PF |
|--------|-----:|---------:|--------:|----:|-------:|----:|
| Bull Trend | 335 | +1.20 | 0.70 | 47.1 | +0.31 | 157.1 |
| Bear Trend | 335 | +1.54 | 0.79 | 50.5 | +0.54 | 122.1 |
| Range/Chop | 335 | +1.22 | 0.71 | 50.7 | +0.36 | 157.1 |
| High Volatility | 335 | +0.31 | 0.21 | 18.4 | −0.12 | 113.8 |
| Crash Recovery | 335 | +1.37 | 0.63 | 43.6 | +0.66 | 162.8 |

**High Volatility is the clear weak spot** (18% win rate, negative Sharpe) — the
strategy struggles when noise dominates, which is the expected and honest result.
It performs best in Bear/Range/Crash-Recovery.

## Where it trades vs. abstains

The most informative finding is *selectivity*: the engine concentrates trading in
liquid majors and **largely abstains on illiquid/obscure assets** — good risk
discipline, not a bug.

**Top 8 by avg return** (high trade counts, healthy win rates):

| Symbol | Avg Ret% | WR% | Trades | Sharpe |
|--------|---------:|----:|-------:|-------:|
| ADA/USDT | +3.65 | 66.9 | 170 | +2.61 |
| DOGE/USDT | +3.59 | 59.3 | 162 | +1.73 |
| AAVE/USDT | +3.11 | 73.7 | 157 | +2.19 |
| LINK/USDT | +2.88 | 62.3 | 150 | +1.66 |
| ATOM/USDT | +2.82 | 70.5 | 157 | +1.78 |
| LTC/USDT | +2.82 | 71.4 | 167 | +1.70 |
| BNB/USDT | +2.80 | 73.3 | 168 | +1.75 |
| DASH/USDT | +2.78 | 61.9 | 144 | +1.68 |

**Bottom 8 by avg return** (note the tiny trade counts — near-total abstention,
returns are just commission drag from a handful of trades):

| Symbol | Avg Ret% | WR% | Trades (25 runs) | Sharpe |
|--------|---------:|----:|-----------------:|-------:|
| B/USDT | −0.02 | 10.0 | 10 | −0.54 |
| CHIP/USDT | −0.02 | 8.0 | 5 | −0.26 |
| SEI/USDT | −0.03 | 20.0 | 19 | −1.04 |
| JUP/USDT | −0.04 | 30.7 | 21 | −1.10 |
| SKYAI/USDT | −0.05 | 8.0 | 8 | −0.39 |
| SIREN/USDT | −0.05 | 8.0 | 6 | −0.39 |
| RAVE/USDT | −0.05 | 4.0 | 6 | −0.44 |
| XPL/USDT | −0.09 | 4.0 | 4 | −0.44 |

The bottom symbols average well under 1 trade per run — the confidence threshold,
regime filter, and per-symbol cooldown keep the engine out of thin/noisy markets.

## On the profit factor

The reported **avg profit factor of 142.6 is a metric artifact, not edge.** It is
the *mean of per-run profit factors*, and runs with very few trades and zero
losers produce an enormous (effectively unbounded) PF that dominates the mean.
This is the same class of issue as the V6.1 metric notes (`ddof=0` Sharpe,
un-annualized Calmar). A median PF or a pooled gross-profit / gross-loss across all
trades would be the honest aggregate. Treat the per-regime PF column as
directional only.

## Reproducing

```bash
python3 run_deep_backtest_full.py   # writes backtest_deep_full_results.json (gitignored)
```

The raw 1.6 MB results JSON is intentionally not committed (regenerable and
non-reproducible per BT-H2). This summary is the durable record.
