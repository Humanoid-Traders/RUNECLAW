# Demo Scenarios

This page provides step-by-step demo scenarios for evaluating RUNECLAW. Each scenario can be executed in under 3 minutes using the Telegram bot or CLI.

---

## Scenario 1: Market Scan and Analysis

**Goal:** Show the full pipeline from market scan to trade idea generation.

### Steps

```text
1. /scan
   → Bot returns top 5 movers with volume spikes and regime labels

2. /analyze BTC
   → Bot runs full analysis pipeline:
      - Fetches 100 hourly candles
      - Computes 10+ technical indicators
      - Detects market regime (TREND/RANGE/CHOP)
      - Generates LLM thesis (or rule-based fallback)
      - Produces TradeIdea with entry, SL, TP, confidence

3. Trade idea appears with [✅ Confirm] [❌ Reject] buttons
   → Tap Confirm to execute paper trade
   → Tap Reject to discard

4. /portfolio
   → Shows updated balance, equity, open positions, PnL
```

### What judges should observe

- Volume spike detection with 2x rolling average threshold
- Regime classification (ADX-14) adapting strategy parameters
- Structured trade idea with explicit entry/exit levels
- Inline keyboard requiring human confirmation
- Portfolio state updating after execution

---

## Scenario 2: Risk Engine Rejection

**Goal:** Demonstrate that the fail-closed risk gate blocks trades when conditions are unsafe.

### Steps

```text
1. /analyze BTC
   → Generate a trade idea (may or may not pass risk)

2. If passed, confirm it. Repeat /analyze on different assets until
   you have 5 open positions (MAX_OPEN_POSITIONS default).

3. /analyze SOL
   → Risk engine REJECTS: "Max open positions exceeded"

4. /rejected
   → Shows the rejected trade with the specific check that failed

5. /risk
   → Shows current exposure, daily PnL, circuit breaker status
```

### What judges should observe

- Specific rejection reason identifying which of 18 checks failed
- No override mechanism -- rejection is final
- `/rejected` command provides transparency into blocked trades
- Risk metrics visible via `/risk`

---

## Scenario 3: Circuit Breaker Trip

**Goal:** Show the circuit breaker halting all trading after a loss threshold is breached.

### Steps

```text
1. Execute several trades via /analyze + Confirm
   → Some will hit stop-loss and close at a loss

2. When cumulative daily loss exceeds 5% (or drawdown exceeds 10%):
   → Circuit breaker automatically trips

3. /analyze BTC
   → REJECTED: "Circuit breaker is active"

4. /status
   → Shows "Circuit Breaker: TRIPPED"

5. /risk
   → Shows daily loss %, drawdown %, breaker status

6. /reset
   → Admin command to manually reset the breaker
   → Trading resumes
```

### What judges should observe

- Automatic halt on loss threshold -- no human intervention needed
- All subsequent trades blocked until manual reset
- Reset requires explicit admin action (audited)
- Circuit breaker state visible across `/status` and `/risk`

---

## Scenario 4: Macro Event Lockdown

**Goal:** Demonstrate the macro calendar blocking trades during high-impact events.

### Steps

```text
1. /macro
   → Shows current macro risk state and upcoming events
   → Example: "State: PRE_EVENT_CAUTION | Next: CPI in 2h 15m"

2. When state is EVENT_LOCKDOWN (30min before to 30min after):
   → /analyze BTC
   → REJECTED: "Macro event gate: EVENT_LOCKDOWN"

3. /rejected
   → Shows macro event as the rejection reason

4. After lockdown window passes:
   → /macro shows "State: POST_EVENT_VOLATILITY" or "NORMAL"
   → /analyze BTC proceeds normally
```

### What judges should observe

- Awareness of real-world macro events (FOMC, CPI, NFP, etc.)
- Automatic trade blocking during high-impact windows
- Fail-closed: if calendar evaluation fails → BLACKOUT → all trades blocked
- Gradual state transitions: NORMAL → CAUTION → LOCKDOWN → POST_EVENT → NORMAL

---

## Scenario 5: Backtest Validation

**Goal:** Run the backtesting engine and show performance metrics.

### Steps

```text
1. /backtest
   → Runs backtest with 720 bars of synthetic data (GBM + GARCH)
   → Returns: total trades, win rate, PnL, max drawdown, Sharpe ratio

2. /backtest 1440 99
   → Custom run: 1440 bars, seed 99
   → Different synthetic data, different results

3. Compare metrics across runs to show consistency
```

### What judges should observe

- Synthetic data generation (not cherry-picked historical data)
- Intrabar SL/TP/trailing stop simulation
- Commission (0.1%) and slippage (0.05%) modeling
- Consistent metrics across seeds (worst DD < 3%, no crashed runs)
- Trailing stops responsible for ~48% of exits with net-positive PnL

---

## Scenario 6: Full Audit Trail

**Goal:** Show that every decision is logged and traceable.

### Steps

```text
1. /analyze ETH → Confirm the trade
2. /analyze SOL → Reject the trade
3. /analyze DOGE → Let risk engine reject it

4. Check audit logs:
   logs/trade.jsonl  → trade ideas, executions, closures
   logs/risk.jsonl   → risk checks, approvals, rejections
   logs/system.jsonl → engine state changes, scan results

5. Each log entry contains:
   - Timestamp (UTC)
   - Action (trade_idea_generated, risk_check, trade_executed, etc.)
   - Result (APPROVED, REJECTED, EXECUTED)
   - Structured data (asset, direction, confidence, checks)
```

### What judges should observe

- Three independent log channels
- Machine-readable JSONL format
- Every decision traceable: generation → risk check → confirmation → execution
- Rejections include the specific reason
- No black boxes -- full transparency

---

## CLI Quick Demo

For judges who prefer terminal output over Telegram:

```bash
# Start CLI mode (no Telegram token needed)
python -m bot.main --mode cli

# At the runeclaw> prompt:
scan_market          # Show top movers
analyze_asset BTC    # Generate trade idea
check_risk           # Show risk status
get_portfolio        # Show portfolio
explain_trade        # Explain last trade decision
quit                 # Exit
```

---

## Test Suite Demo

```bash
# Run all 180 tests
pytest tests/test_core.py -v

# Key test categories:
# - Risk engine: all 18 checks, circuit breaker, edge cases
# - Portfolio: position lifecycle, PnL, drawdown
# - Analyzer: indicators, candlestick patterns, Fibonacci, OBV, VWAP
# - Backtest: replay engine, trailing stops, commission/slippage
# - Macro: calendar states, boundary conditions, fail-closed
# - Integration: full pipeline, rejection flows, concurrent access
```
