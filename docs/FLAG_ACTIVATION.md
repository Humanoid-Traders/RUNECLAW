# Flag Activation Runbook

> **Status (2026-06): all deep-audit flags are now ON by default in code.** The
> operator activated the full set, staged in the order below. Each flag's
> in-code default is now ON, so a fresh deploy runs with everything enabled —
> **no `.env` edit needed**. To DISABLE any one, set it to `0`/`false` in your
> `.env` (process env > `.env` > in-code default) and **restart the bot**. Run
> `/flags` in Telegram to see the live ON/OFF state (it reads the effective
> config).

The sections below document each flag, what it does, and the order it was
enabled. They are kept as the canonical reference and as the **disable** guide.

> Live-money note: the **signal-changing** group alters which trades fire and the
> **judgment/sizing** group changes sizing/TA — backtest (`python -m
> bot.backtest.runner`) if you want to compare against the legacy behaviour
> before relying on them. For a reproducible legacy-vs-new comparison across many
> seeds (and walk-forward), run `python scripts/flag_compare.py` (see
> `--walk-forward N`, `--seeds`, `--json`). On synthetic data the robust effect is
> the risk-sizing change (per-strategy cap + regime sizing) shrinking notional;
> OF_* / learning flags need real L2 / history to show. The learning nudges fail
> open (identity) until enough closed setups accumulate.

---

## Already active (no flag needed)

These correctness / durability / display fixes are live as soon as the code is
deployed — nothing to set:

- fsync-on-replace durability (portfolio / engine / risk state survive a crash)
- emergency-position records the actual leverage; dynamic-leverage is unified &
  reduce-only; check #2 is a fail-closed invariant
- tick-rule aggressor inference; `/strategy` shows the real regime
- closed-trade outcomes are tagged with the **real** detected regime (so the
  learning data is correct once you enable the learners)
- backtest result stamps `data_source` / `used_synthetic`
- REST ticker staleness guard (`LIVE_TICKER_MAX_AGE_SEC=120`) and WS tick-age
  guard (`WS_MAX_TICK_AGE_SEC=15`) are **on by default**

---

## 1. Safety / observability — ✅ now ON by default

Pure tightenings; they can only make the bot safer or more accurate. **These now
default ON in code** (operator-requested activation) — listed here for reference;
set any to `0`/`false` in `.env` to disable.

```dotenv
WS_IDLE_TIMEOUT_SEC=90              # reconnect a silently-stalled WS feed (default 90)
VERIFY_CLASSIC_SLTP_ON_RESTART=1   # re-place a lost SL/TP leg after a restart (default ON)
LLM_FALLBACK_COST_ACCOUNTING=1     # count fallback LLM calls against daily budgets (default ON)
OF_GUARD_TOP_DEPTH_ENABLED=1       # require real top-of-book depth ≥ position size (default ON)
LLM_CACHE_SCOPED_KEY=1             # scope LLM cache key by model/tier (default ON)
```

## 2. Signal-changing — ✅ now ON by default

Each is correct, but alters which trades fire. **These now default ON in code**
(operator-requested activation). Set any to `0`/`false` in `.env` to disable, and
backtest (`python -m bot.backtest.runner`, honest `data_source`) to compare.

```dotenv
OF_FUNDING_VOTE_FIXED_SCALE=1      # funding confluence vote actually contributes (default ON)
VWAP_SESSION_ANCHORED=1            # vwap voters use session-anchored VWAP (default ON)
LEADING_DIAGONAL_PRETREND_FIX=1    # stricter leading-diagonal detection (default ON)
LIQUIDITY_SWEEP_OWN_CLOSE=1        # stricter liquidity-sweep detection (default ON)
OF_TIME_BARS_ENABLED=1             # taker 3-bar gate becomes time-aware (default ON)
PATTERN_ATR_TOLERANCES_ENABLED=1   # H&S / double-top symmetry tolerance scales with
                                   # ATR — only ever tightens the fixed 5%/3% gate (default ON)
```

## 3. Learning — ✅ now ON by default

The write (paper/sim closes → learners) and the apply nudges are **now default ON
in code** (operator-requested activation). Note the nudges only bite once enough
closed setups have accumulated — they fail open (identity) below the minimum
sample count — so enabling them before history builds is harmless. Set any to
`0`/`false` in `.env` to disable.

```dotenv
LEARN_FROM_PAPER_CLOSES=1          # feed paper/sim closes to the learners (default ON)
SETUP_EXPECTANCY_ENABLED=1         # apply the per-setup expectancy nudge (default ON)
CONFIDENCE_CALIBRATION_ENABLED=1   # apply confidence calibration (default ON)
ADAPTIVE_CONFIDENCE_ENABLED=1      # apply the adaptive-confidence nudge (default ON)
LEARNING_AUTO_REFIT_ENABLED=1      # auto-refit the learners on closed trades (default ON)
```

## 4. Judgment calls — ✅ now ON by default

These change sizing / TA behaviour and are **now default ON in code**
(operator-requested activation). Set any to `0`/`false` in `.env` to disable.

```dotenv
DAILY_LOSS_BREAKER_AUTORESET=1     # auto-resume after a bad day rolls over (default ON)
DROP_UNCLOSED_CANDLE_ENABLED=1     # compute TA on closed candles only — repaint fix (default ON)
REGIME_SIZING_ENABLED=1            # apply regime→sizing multipliers, fills _current_regime (default ON)
PER_STRATEGY_NOTIONAL_CAP_ENABLED=1  # size cap + POSITION_SIZE check use the per-strategy
                                     # notional ceiling (scalp 8% / intraday 10% / swing 13% /
                                     # position 15%) instead of the global 13% (default ON)
```

## 5. Backtest fidelity (backtest runs only — no live effect)

These change only what the backtest models; they never touch live trading.

```dotenv
BACKTEST_PARTIAL_TP=1             # backtest scales out through the live partial-TP
                                  # ladder (TP1 50% @1.5R + SL→breakeven, TP2 30%
                                  # @2.5R + lock 1R, runner 20% ATR-trail) instead
                                  # of a single full exit. Default OFF keeps legacy
                                  # backtest numbers byte-identical; turn ON to make
                                  # backtest win-rate / R:R reflect live exits.
```

`BACKTEST_PARTIAL_TP` stays opt-in (a backtest-run choice, not a live setting).

**Order-flow replay (#17) — recording now ON by default.** The backtest runs the
analyzer with no order flow, so the smart-money voter / order-flow confluence /
veto / funding haircut never fire — backtest signals diverge from live. To close
the gap the LIVE bot now **shadow-records** each computed order-flow snapshot by
default (write-only, no signal effect; accumulates
`data/learning/order_flow_snapshots.jsonl`):

```dotenv
OF_RECORD_SNAPSHOTS=1             # live order-flow shadow-recording (default ON; set 0 to disable)
# OF_SNAPSHOT_PATH=data/learning/order_flow_snapshots.jsonl   # optional override
```

Then run the backtest with `--use-recorded-order-flow` (or
`BacktestConfig.use_recorded_order_flow=True`). It replays the most recent
snapshot at/before each bar; with no recording the analyzer simply runs without
order flow, identical to the legacy backtest (the runner prints how many
snapshots loaded so an empty replay isn't mistaken for the real thing):

```bash
python -m bot.backtest.runner --use-recorded-order-flow          # default JSONL path
python -m bot.backtest.runner --use-recorded-order-flow \
    --of-snapshot-path data/learning/order_flow_snapshots.jsonl  # explicit path
python -m bot.backtest.runner --use-recorded-order-flow --walk-forward 5  # also per-fold
```

---

*This file is the human-readable companion to the `/flags` command, which reads
the same effective configuration.*

---

## 8. Autonomous live execution — ✅ operator-activated (2026-07)

The operator enabled hands-off live trading. In-code defaults now:

```dotenv
AUTO_CONFIRM_THRESHOLD=0.85        # auto-execute signals at/above the 0.85 admin bar
AUTO_CONFIRM_LIVE_ENABLED=1        # allow real-money auto-execution (no human tap)
AUTO_CONFIRM_USE_CALIBRATED=1      # gate the 0.85 on CALIBRATED confidence (only tightens)
```

The extra protective breakers (`EQUITY_CURVE_BREAKER_ENABLED`,
`DRAWDOWN_RECOVERY_ENABLED`) were considered but left **OFF**: they are unproven
and too conservative for auto-trading (drawdown-recovery imposes a 0.85
confidence floor whenever the account is even slightly underwater, blocking
legitimate entries — it fails the red-team position-flood scenarios). Enable them
in `.env` only if you specifically want that behaviour.

**Security note:** the RC-AUD-002 live gate is unchanged — real-money auto-execution
still flows through the explicit `auto_confirm_live_enabled` check in
`engine._tick`; only its default flipped, by deliberate operator choice. To
return to fail-closed (manual confirmation), set `AUTO_CONFIRM_LIVE_ENABLED=0`
(or `AUTO_CONFIRM_THRESHOLD=1.0`) in `.env` and restart.

**Bounded by:** the calibrated 0.85 bar, per-strategy risk sizing, daily-loss +
max-drawdown circuit breakers, the volatility guard, correlation caps, and
loss-streak cooldown already in force. These bound downside; they do not create
edge — OOS validation (#221) found the benchmark edge regime-specific, so monitor
live P&L and disable if it bleeds.

---

## 9. MTF-alignment gate — ✅ operator-activated (2026-07)

```dotenv
MTF_ALIGNMENT_GATE_ENABLED=1   # reject counter-trend entries vs the daily-weighted HTF trend (default ON)
```

Risk gate #19 was historically **dead** (it parsed `MTF:1h=UP` tags that nothing
produced, so it skipped every trade). It was revived direction-aware: reject a
LONG when the higher-timeframe EMA20/50 trend (1h/4h/1d, daily-weighted) is
bearish, a SHORT when it is bullish; neutral/unknown → no opinion.

**Why ON:** A/B on `corr_dense_1h` (`--honest`, 16-month): removed exactly one
counter-trend loser and kept all six winners — +1.40%→+1.66%, PF 1.87→**2.23**.
Neutral on `alts_1h_v2` (no counter-trend entries in that sample). Evidence is
thin (single-loser delta) but strictly non-harmful in both tests and the
mechanism is principled (don't fight the daily trend).

**Not activated from the same batch** (A/B **inert** — byte-identical results,
left OFF): `CANDLE_ENTRY_VETO_ENABLED`, `STRUCTURE_TRAIL_ENABLED`,
`REENTRY_COOLDOWN_ENABLED`, `FEE_AWARE_ENTRY_GATE_ENABLED` (the latter two bite
only on scalp-heavy churn; see `docs/FEE_REDUCTION.md`).

To disable: `MTF_ALIGNMENT_GATE_ENABLED=0` in `.env` and restart.
