# ⚔️ RUNECLAW — Staged Live-Hardening Runbook

You're live. Almost every protective/learning feature shipped recently is
**default-OFF**, so the bot is currently running *without* them. This is the
order to turn them on, what each does, and what to watch after each step.

## Golden rules
- **One flag at a time.** Flip it, restart, watch for a defined window, then move on.
- **All of these are `.env` flags read at launch** — edit `.env` and **restart** the bot.
- **Rollback = set the flag back to `false` (or remove it) and restart.** Every
  feature here is strictly additive and gated, so reverting always returns to the
  prior behaviour. No data is lost.
- **Watch via Telegram:** `/health` (vitals), `/status` (engine + rejections),
  `/livepositions` (open live trades + SL/TP), `/slippage` (execution quality),
  `/positions`, `/calibration` (learner readiness), `/whynot SYMBOL` (why a trade
  was skipped).
- **Emergency controls (admin):** the kill-switch covers **every** account
  (operator + all per-user), not just the operator:
  - **Emergency Stop** (button / confirm) — trips the circuit breaker on **all**
    risk engines, clears queued ideas, and flattens open positions on **every**
    account. Say "resume" / `/reset` to clear all breakers and restart.
  - `/halt` — pauses **new** trades on all accounts (does not flatten).
  - `/closeall` — flattens open positions on all accounts (does not halt).

---

## Stage 0 — Confirm the baseline is live (no flags to flip)
These are already ON and should be verified before hardening:
- **Unprotected-position alert** — CRITICAL ping if any live position ever lacks an
  exchange stop past the grace window.
- **Slippage guard** (`SLIPPAGE_GUARD_ENABLED=true`) — flattens an over-slipped fill.
- **Unprotected escalation** (`UNPROTECTED_ESCALATION_ENABLED=true`) + SL/TP self-heal.
- **Proactive alerts** — drawdown tiers, WS health, stale balance, tick failures,
  macro lockdown, slippage drift.

**Watch:** run `/health` and `/livepositions` — confirm every open position shows a
SL **and** TP "on exchange". If anything is unprotected, fix that *before* hardening.

---

## Stage 1 — Risk tightening (turn on NOW — deterministic, no data needed)
These reduce risk immediately and don't depend on any learned data.

```bash
LIVE_RISK_HARDENING_ENABLED=true     # forces correlation-sizing + covariance-VaR ON
LIVE_MAX_DRAWDOWN_PCT=7              # tighter live DD cap than the 10% paper default
REGIME_HARD_GATES_ENABLED=true       # no-trade in CHOP/UNKNOWN; block counter-trend in strong trends
TIME_STOP_LIVE_AUTO_CLOSE=true       # auto-close dead/invalidated theses instead of riding to SL
```

**Effect:** fewer but cleaner trades; correlated stacking is sized down; the lowest-edge
regimes stop trading; failed setups exit early.
**Watch (a few days):** `/status` rejection reasons (you'll see `CORRELATION`, regime,
and time-exit actions), drawdown alerts, and that win rate / R isn't hurt by the regime
gates. If the regime gates feel too tight, raise `REGIME_STRONG_ADX` (e.g. 35) or revert.

---

## Stage 2 — Start the learning flywheel (turn on, then WAIT for data)
The learners need closed-trade history before they mean anything.

```bash
LEARNING_AUTO_REFIT_ENABLED=true            # refit calibration + voter-weights + expectancy from closed trades
UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED=true    # cap the unproven LLM's blend weight UNTIL calibration lands
LIVE_PERFORMANCE_GOVERNOR_ENABLED=true      # de-risk automatically when REALIZED results degrade (needs ~10+ closes)
```

**Effect:** the learners accrue their curves in **shadow** (logged, not yet applied);
the LLM can't dominate sizing while its confidence is still unproven (this cap
auto-lifts the moment calibration is enabled in Stage 3). The **performance
governor** is a separate, deterministic backstop: it watches the realized win
rate + net PnL of your most recent closed trades and, once ≥`LIVE_PERF_MIN_SAMPLES`
(default 10) have accrued, **shrinks size** when the recent window underperforms
and **pauses trading** if it's both losing often *and* net-negative. It can only
tighten — no effect while results are healthy or before enough trades exist.
Tune `LIVE_PERF_REDUCE_WINRATE` / `LIVE_PERF_PAUSE_WINRATE` if the defaults feel
too eager; `/whynot SYMBOL` shows `LIVE_PERF_GOVERNOR` when it acts.
**Watch:** let this run until you have a meaningful sample of **closed** trades
(≈50–100+). Check `/calibration` — it tells you when the calibrator `is_ready`.
**Do not proceed to Stage 3 until the calibrator reports ready.**

---

## Stage 3 — Apply the learned overlays (only after Stage 2 has data)
Enable these **one at a time**, watching between each. Order matters.

```bash
CONFIDENCE_CALIBRATION_ENABLED=true   # 1) confidence now reflects realized win rate
AUTO_CONFIRM_USE_CALIBRATED=true      # 2) the 0.85 admin auto-trade fires on the MEASURED win rate
VOTER_WEIGHT_LEARNING_ENABLED=true    # 3) reweight confluence voters by realized edge
SETUP_EXPECTANCY_ENABLED=true         # 4) nudge confidence by per-(symbol,regime,direction) history
```

**The big one is #2** — with it, an over-optimistic 0.90 idea whose *calibrated* value is
0.78 is held for manual confirm instead of being auto-placed with real money. It can only
*tighten* auto-trade.
**Watch:** auto-trade frequency should **drop** if the LLM was overconfident (this is the
point); win rate of *executed* trades should rise. Confirm `/calibration` and the
confidence distribution look sane. Re-refit periodically (auto-refit handles it).

---

## Stage 4 — Optional enhancers (lowest priority, enable last)
```bash
FUNDING_COST_AWARE_ENABLED=true   # haircut confidence when a swing would PAY adverse funding
EXTERNAL_SENTIMENT_ENABLED=true   # adds a Fear&Greed contrarian voter (makes a network call)
# Once calibration shows the rule engine ≈ the LLM, lean on the deterministic side:
# LLM_BLEND_WEIGHT=0.4
# CONFLUENCE_BLEND_WEIGHT=0.6
```
Each is bounded and reversible. Enable only after Stages 1–3 are stable.

---

## Position sizing (independent of the stages)
The live caps are MARGIN figures, hard-enforced in the executor. Start small and scale:
```bash
MICRO_MAX_POSITION_USD=100      # raise gradually as you trust the live behaviour
MICRO_MAX_TOTAL_EXPOSURE=500
MICRO_MAX_OPEN_POSITIONS=5
```
Raise these **after** Stage 1, not before — the risk tightening should be on first.

---

## Quick reference — recommended end state
| Stage | Flags | When |
|---|---|---|
| 1 | `LIVE_RISK_HARDENING_ENABLED`, `REGIME_HARD_GATES_ENABLED`, `TIME_STOP_LIVE_AUTO_CLOSE` | now |
| 2 | `LEARNING_AUTO_REFIT_ENABLED`, `UNCALIBRATED_LLM_WEIGHT_CAP_ENABLED`, `LIVE_PERFORMANCE_GOVERNOR_ENABLED` | now, then wait ~50–100 closes |
| 3 | `CONFIDENCE_CALIBRATION_ENABLED` → `AUTO_CONFIRM_USE_CALIBRATED` → `VOTER_WEIGHT_LEARNING_ENABLED` → `SETUP_EXPECTANCY_ENABLED` | after calibrator ready |
| 4 | `FUNDING_COST_AWARE_ENABLED`, `EXTERNAL_SENTIMENT_ENABLED`, blend-weight tuning | last |

If anything misbehaves: set the offending flag back to `false`, restart, and report
what you saw — every step is independently reversible.

---

## Deployment-host hygiene (ops)

Two host-level practices that are outside this repository's control but bite
live operators:

1. **Pin the Python runtime.** The repo now ships `.python-version` (3.11) and
   `pyproject.toml` declares `requires-python = ">=3.11"`. Use `pyenv`/`uv` (or
   your image's base tag) so the host resolves the same interpreter — silent
   3.10 fallbacks break `datetime.UTC` and modern typing at import time.

2. **Keep any watchdog/restart script OUTSIDE the repo working tree.** If your
   deploy loop uses `git reset --hard`/`git clean` to update the checkout, any
   watchdog script stored inside the tree loses local edits and (on some
   setups) its execute bit. Install it to `/usr/local/bin` (or a systemd unit)
   instead, or re-assert `chmod +x` as a post-reset step in the deploy script.
   Nothing inside this repository performs `git reset` — this is purely about
   the host-side update loop some operators run.
