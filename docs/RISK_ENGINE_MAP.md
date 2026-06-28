# RUNECLAW Risk Engine Map

Companion to `docs/SIGNALS_MAP.md`. How a `TradeIdea` that cleared the analyzer
is then gated by risk: the pre-trade checks, position sizing, circuit breakers,
and the fail-closed contract. Anchors are at time of writing — trust the
function/class names if they drift.

> **Core contract (fail-closed):** every check that cannot be *evaluated*
> results in **REJECTED**. Risk never has to prove a trade is dangerous; the
> trade has to prove it is safe. Entry point: `RiskEngine.evaluate()`
> (`bot/risk/risk_engine.py` ~L450).

## Pre-trade checks

`evaluate()` runs ~23 checks; any failure rejects. Grouped:

**Account-health gates**
- **Circuit breaker** — hard stop after consecutive losses / daily-loss / drawdown
  breach; persisted to `data/risk_state.json` (corrupt file ⇒ assume tripped).
- **Warning-rate breaker** — halts if infrastructure exceptions fire >5×/hour.
- **Daily loss limit** (`MAX_DAILY_LOSS_PCT`, 5%) — trips the circuit on breach.
- **Max drawdown** (`MAX_DRAWDOWN_PCT`, 10%) — trips the circuit on breach.
- **Consecutive-loss streak** (`MAX_CONSECUTIVE_LOSSES`) — soft reject below the
  hard circuit.
- **Cooldown after loss** (`COOLDOWN_AFTER_LOSS_SEC`, 120s).

**Sizing / exposure gates**
- **Position size / margin cap** (`MAX_POSITION_PCT`) — fixed-fractional by stop
  distance, hard-capped at a % of equity (margin, not notional).
- **Leverage-aware margin risk** (`MAX_MARGIN_RISK_PCT`, 30%) — SL distance ×
  leverage must stay under the cap; dynamic leverage reduction available.
- **Max open positions** (`MAX_OPEN_POSITIONS`, 5).
- **Portfolio exposure** (`MAX_PORTFOLIO_EXPOSURE_PCT`, 80%).
- **Per-symbol exposure** (`MAX_SYMBOL_EXPOSURE_PCT`, 20%).
- **Correlation / concentration** (`MAX_CORRELATION_PER_GROUP`, 2) — 45+ mapped
  groups (BTC, ETH, ALT_L1, MEME, SOLANA_ECO, DEFI, L2, AI, …); unmapped alts
  pooled with their own cap.
- **PCA concentration** — reject if PC1 eigenvalue > 70% of variance.
- **Portfolio VaR** (`MAX_PORTFOLIO_VAR_PCT`, 15%; covariance opt-in).

**Signal-quality gates**
- **Risk:reward** (`MIN_RISK_REWARD`, 1.2) — skipped for user-confirmed limits.
- **Confidence threshold** (`MIN_CONFIDENCE`, 0.55) — manual trades skip.
- **Stop-loss required** — reject if SL = entry or invalid.
- **Entry-price sanity** — reject NaN/inf/non-positive.
- **Stale-data guard** (`STALE_DATA_MAX_AGE_SEC`, 300s; 2× for limits) + clock skew.
- **Volatility guard** (`VOLATILITY_GUARD_ATR_PCT`, 7%; 10% for memes) — fail-closed.
- **Macro event risk** — size throttle / lockdown windows; 0.5× fallback on error.
- **Multi-timeframe alignment** — 2-of-3 consensus; graceful skip if absent.
- **Order-flow gates** — taker 3-bar gate, bid dominance ≥ 2:1 for longs (fail-open).

## Position sizing (pre-cap multipliers)

Base: `risk_budget = equity × MAX_POSITION_PCT / stop_distance_pct`, then a hard
notional cap. The base is then scaled by (all *tighten-only* where noted):

- **Regime multiplier** — CHOPPY 0.5×, STRONG_TREND 1.5×, HIGH_VOL 0.3×, etc.
- **Kelly (opt-in, `KELLY_SIZING_ENABLED`)** — half-Kelly from realized history,
  **tighten-only**; needs ≥20 closed trades.
- **Macro throttle** — provider REDUCE multiplier; 0.5× fallback on error.
- **Session scaling** — low-liquidity-session multiplier.
- **Equity-curve breaker** — 1.0 / 0.5 (below MA) / 0.0 (below 2σ).
- **Drawdown recovery** — 0.5× when DD > 70% of max; requires ≥0.85 confidence.

## State & persistence

- `data/risk_state.json` — circuit breaker, loss streak, daily PnL; atomic
  temp-file+replace; corrupt ⇒ assume tripped (fail-closed).
- Trade-close callback feeds realized PnL back into streak tracking.

## Key anchors

| Concept | Location |
|---|---|
| `RiskEngine` | `bot/risk/risk_engine.py:196` |
| `evaluate()` (all checks) | `risk_engine.py:450` |
| Circuit breaker state/trips | `risk_engine.py:216`, `262`, `_trip_circuit_breaker` |
| Equity-curve breaker | `risk_engine.py:~371` |
| Sizing + multipliers | `risk_engine.py:~581` (in `evaluate`) |
| Kelly sizing | `risk_engine.py:~1112` |
| Regime multipliers | `risk_engine.py:~1269` |
| Correlation groups | `risk_engine.py:~138` (map), `~1653` (check) |
| PCA concentration | `risk_engine.py:~1321` |
| Portfolio VaR | `risk_engine.py:~1440` |
| Limits config | `bot/config.py` · `RiskLimits` (~L137) |

## Gaps (see SIGNALS_MAP for the ranked list)

No mean-variance optimizer (PCA/VaR only), no per-position/sector drawdown
attribution, static correlation groups (no live recompute), VaR uses a per-trade
return proxy (H-05). Risk parameters are frozen at import (no hot reload).
