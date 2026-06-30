# Flag Activation Runbook

The 2026 deep-audit fixes ship **gated default-OFF** so live trading behaviour
never changes without an explicit operator decision. This runbook lists every
opt-in flag, what it does, and the recommended order to enable them. Set a flag
in your `.env` (or deployment environment) and **restart the bot** for it to take
effect. Run `/flags` in Telegram any time to see the current ON/OFF state.

> Live-money rule of thumb: enable the **safety/observability** group freely;
> **backtest** the signal-changing group before flipping it on real money; turn
> on the **learning write** early so history accumulates, and only apply the
> learning nudges once there is enough data.

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

## 1. Safety / observability — recommended ON

Pure tightenings; they can only make the bot safer or more accurate.

```dotenv
WS_IDLE_TIMEOUT_SEC=90              # reconnect a silently-stalled WS feed
VERIFY_CLASSIC_SLTP_ON_RESTART=1   # re-place a lost SL/TP leg after a restart
LLM_FALLBACK_COST_ACCOUNTING=1     # count fallback LLM calls against daily budgets
OF_GUARD_TOP_DEPTH_ENABLED=1       # require real top-of-book depth ≥ position size
LLM_CACHE_SCOPED_KEY=1             # only needed if you run multi-user LLM tiers
```

## 2. Signal-changing — backtest first

Each is correct, but alters which trades fire. Run
`python -m bot.backtest.runner` (now with honest `data_source`) and compare
before enabling on live money.

```dotenv
OF_FUNDING_VOTE_FIXED_SCALE=1      # funding confluence vote actually contributes
VWAP_SESSION_ANCHORED=1            # vwap voters use session-anchored VWAP
LEADING_DIAGONAL_PRETREND_FIX=1    # stricter leading-diagonal detection
LIQUIDITY_SWEEP_OWN_CLOSE=1        # stricter liquidity-sweep detection
OF_TIME_BARS_ENABLED=1             # taker 3-bar gate becomes time-aware
PATTERN_ATR_TOLERANCES_ENABLED=1   # H&S / double-top symmetry tolerance scales with
                                   # ATR (only ever tightens the fixed 5%/3% gate)
```

## 3. Learning — enable the write now, apply later

Turn on the write so the simulation-first history accumulates; once there are
enough closed setups, enable the apply flags.

```dotenv
LEARN_FROM_PAPER_CLOSES=1          # start feeding paper/sim closes to the learners
# …then, once history builds:
SETUP_EXPECTANCY_ENABLED=1         # apply the per-setup expectancy nudge
CONFIDENCE_CALIBRATION_ENABLED=1   # apply confidence calibration
ADAPTIVE_CONFIDENCE_ENABLED=1      # apply the adaptive-confidence nudge
LEARNING_AUTO_REFIT_ENABLED=1      # auto-refit the learners on closed trades
```

## 4. Judgment calls (your decision)

```dotenv
DAILY_LOSS_BREAKER_AUTORESET=1     # auto-resume after a bad day rolls over (vs manual /reset)
DROP_UNCLOSED_CANDLE_ENABLED=1     # compute TA on closed candles only (repaint fix)
REGIME_SIZING_ENABLED=1            # apply regime→sizing multipliers (also fills _current_regime)
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

**Order-flow replay (#17).** The backtest runs the analyzer with no order flow, so
the smart-money voter / order-flow confluence / veto / funding haircut never fire
— backtest signals diverge from live. To close the gap, *shadow-record* live order
flow, then replay it in the backtest:

```dotenv
# 1. On the LIVE bot — log each computed order-flow snapshot (write-only, no
#    signal effect). Accumulates data/learning/order_flow_snapshots.jsonl.
OF_RECORD_SNAPSHOTS=1
# OF_SNAPSHOT_PATH=data/learning/order_flow_snapshots.jsonl   # optional override
```

Then run the backtest with `BacktestConfig.use_recorded_order_flow=True` (or the
runner's equivalent flag). It replays the most recent snapshot at/before each
bar; with no recording the analyzer simply runs without order flow, identical to
the legacy backtest.

---

*This file is the human-readable companion to the `/flags` command, which reads
the same effective configuration.*
