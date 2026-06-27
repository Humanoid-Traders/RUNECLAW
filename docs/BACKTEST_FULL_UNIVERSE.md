# Full-Universe Deep Backtest

**Date:** 2026-06-27
**Harness:** `run_deep_backtest_full.py`
**Scope:** the entire 67-symbol scan universe (`bot/skills/scan_skill.py`) ×
5 market regimes × 5 seeds = **1,675 runs**, 1,500 1H bars each (~62 days/run).
**Runtime:** ~2,080 s across 3 worker processes, per-bar audit logging suppressed.

> **Now reproducible and accurately costed.** This run uses the BT-H1 and BT-H2
> fixes (`docs/AUDIT_REPORT_V6.1.md`): the backtest charges the commission it
> reports (0.10% taker/side, previously a silently-ignored 0.06%), and session
> adjustments use the simulated bar time rather than the wall clock — so the same
> seed now yields identical results regardless of when it is launched. Numbers
> here differ from the first (pre-fix) run, which had been launched during a
> low-liquidity "session" that uniformly penalised every bar; that artifact is
> gone. Results still run on *synthetic* data and are **not** evidence of live
> edge — they show the pipeline runs and the risk gates fire across the universe.

## Headline

| Metric | Value |
|--------|-------|
| Valid runs | **1,675 / 1,675 (0 errors)** |
| Total trades | 6,905 |
| Profitable runs | 987 / 1,675 (58.9%) |
| Symbols with >0 avg return | 59 / 67 (median per-symbol +1.64%) |
| Avg return | **+2.20%** (best +32.72%, worst −1.64%) |
| Avg max drawdown | 0.82% (worst 2.78%) |
| Crashed runs (DD>20%) | **0** |
| Avg win rate | 50.1% |
| Avg Sharpe / Sortino | +1.21 / +1.06 |
| Avg profit factor | 127.0 *(metric artifact — see below)* |
| Total commission (0.10%) | $18,239 |

**Zero errors across all 67 symbols** confirms the BT-CRASH-1/2 fixes hold at
scale; **same-seed determinism** is enforced by `tests/test_backtest_validity.py`
(proven independent of the wall-clock hour).

## By regime

| Regime | Runs | Avg Ret% | Avg DD% | WR% | Sharpe | PF |
|--------|-----:|---------:|--------:|----:|-------:|----:|
| Bull Trend | 335 | +2.30 | 0.91 | 52.5 | +1.20 | 128.3 |
| Bear Trend | 335 | +2.88 | 0.98 | 57.2 | +1.68 | 93.4 |
| Range/Chop | 335 | +2.75 | 0.95 | 59.1 | +1.65 | 128.9 |
| High Volatility | 335 | +0.64 | 0.37 | 24.2 | +0.07 | 147.0 |
| Crash Recovery | 335 | +2.42 | 0.87 | 57.6 | +1.46 | 137.2 |

**High Volatility remains the clear weak spot** (24% win rate, ~flat Sharpe) — the
strategy struggles when noise dominates, the expected honest result. It performs
best in Bear/Range/Crash-Recovery.

## Where it trades vs. abstains

The most informative finding is *selectivity*: the engine concentrates trading in
liquid majors and **largely abstains on illiquid/obscure assets** — good risk
discipline, not a bug.

**Top 6 by avg return** (high trade counts, healthy win rates):

| Symbol | Avg Ret% | WR% | Trades | Sharpe |
|--------|---------:|----:|-------:|-------:|
| ADA/USDT | +6.91 | 65.6 | 241 | +3.85 |
| AAVE/USDT | +5.23 | 74.6 | 205 | +3.35 |
| XRP/USDT | +5.16 | 79.1 | 213 | +3.57 |
| LINK/USDT | +4.98 | 70.1 | 201 | +3.14 |
| ETC/USDT | +4.89 | 68.1 | 204 | +2.68 |
| BTC/USDT | +4.88 | 76.9 | 215 | +3.20 |

**Bottom 6 by avg return** (tiny trade counts — near-total abstention; returns are
just commission drag from a handful of trades over 25 runs):

| Symbol | Avg Ret% | WR% | Trades (25 runs) | Sharpe |
|--------|---------:|----:|-----------------:|-------:|
| SIREN/USDT | −0.06 | 16.7 | 14 | −0.44 |
| RAVE/USDT | −0.06 | 16.7 | 14 | −0.44 |
| M/USDT | −0.06 | 20.7 | 19 | −0.80 |
| B/USDT | −0.06 | 20.7 | 19 | −0.80 |
| XPL/USDT | −0.07 | 10.0 | 12 | −0.72 |
| PENGU/USDT | −0.09 | 18.7 | 19 | −0.63 |

The bottom symbols average under 1 trade per run — the confidence threshold,
regime filter, and per-symbol cooldown keep the engine out of thin/noisy markets.

## On the profit factor

The reported **avg profit factor of 127 is a metric artifact, not edge.** It is
the *mean of per-run profit factors*, and runs with very few trades and zero
losers produce an enormous (effectively unbounded) PF that dominates the mean. A
median PF, or a pooled gross-profit / gross-loss across all trades, would be the
honest aggregate. Treat the per-regime PF column as directional only. (This is the
same class of issue as the remaining V6.1 metric notes: `ddof=0` Sharpe,
un-annualized Calmar, breakeven-as-loss — all still documented, not yet fixed.)

## Reproducing

```bash
python3 run_deep_backtest_full.py   # writes backtest_deep_full_results.json (gitignored)
```

With the BT-H2 fix, a given seed produces identical results on every run. The raw
1.6 MB results JSON is intentionally not committed (regenerable); this summary is
the durable record.

## Pre-fix vs post-fix (why the numbers moved)

| Metric | Pre-fix run | Post-fix run |
|--------|------------:|-------------:|
| Avg return | +1.13% | +2.20% |
| Avg win rate | 42.1% | 50.1% |
| Avg Sharpe | +0.35 | +1.21 |
| Total trades | 4,727 | 6,905 |
| Commission | $6,557 (0.06%) | $18,239 (0.10%) |
| Reproducible | no (wall-clock) | **yes** |

The pre-fix run was launched during a low-liquidity session window, so the
wall-clock session penalty (size ×0.75, confidence −0.03) was applied uniformly to
every bar, suppressing trades and confidence. The post-fix run lets each simulated
bar carry its own session, so the effect averages out — more trades clear the
confidence gate, and the higher (honest) commission is more than offset. The
post-fix numbers are the reproducible reference.
