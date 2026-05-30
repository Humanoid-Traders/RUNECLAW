# Risk Framework

RUNECLAW's risk engine is the most critical component in the system. It follows a **fail-closed** design: if any check cannot be evaluated or fails, the trade is rejected. There are no overrides, no force-execute flags, no backdoors.

## Design Philosophy

### Fail-Closed, Not Fail-Open

Traditional trading bots often fail open -- if a risk check errors out, the trade proceeds. RUNECLAW inverts this:

- If the risk engine throws an exception, the trade is aborted.
- If any single check fails, the entire evaluation returns REJECTED.
- If market data is unavailable, the check cannot pass, so the trade is blocked.

This means the system may miss opportunities. That is an acceptable trade-off. Missing a trade is recoverable. Taking a bad trade is not.

### Defense in Depth

Risk is enforced at multiple layers:

1. **Analyzer level** -- Ideas with blended confidence below 0.60 are never generated.
2. **Risk engine level** -- 18 independent checks must all pass.
3. **Confirmation level** -- Risk is re-evaluated when the human confirms (the market may have moved).
4. **Configuration level** -- `SIMULATION_MODE=true` and `LIVE_TRADING_ENABLED=false` are both set by default. Live trading requires both flags to be flipped.

## The Eighteen Risk Checks

Every `TradeIdea` must pass all eighteen checks before it enters the pending queue.

### 1. Circuit Breaker

**Check:** Is the circuit breaker active?

If the circuit breaker has been tripped by a prior event (daily loss or drawdown breach), all new trades are rejected until a human manually resets it.

### 2. Position Size

**Check:** Does the proposed notional position size exceed 20% of equity?

| Parameter | Default | Description |
|-----------|---------|-------------|
| Notional cap | 20% | Maximum single position as % of equity |

A $10,000 portfolio means no single position can exceed $2,000 notional.

### 3. Daily Loss

**Check:** Has today's cumulative loss (realized + unrealized) exceeded `MAX_DAILY_LOSS_PCT`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DAILY_LOSS_PCT` | 5.0% | Maximum daily loss as % of equity |

Daily PnL includes both closed-trade losses and mark-to-market unrealized losses on open positions. This means a temporary adverse price spike against open positions can trip the breaker. This is intentional — the system errs on the side of caution.

If breached, the circuit breaker is automatically tripped.

### 4. Maximum Drawdown

**Check:** Has the portfolio drawdown from peak equity exceeded `MAX_DRAWDOWN_PCT`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DRAWDOWN_PCT` | 10.0% | Maximum drawdown from peak equity |

If breached, the circuit breaker is automatically tripped.

### 5. Open Positions Limit

**Check:** Is the number of open positions at or above `MAX_OPEN_POSITIONS`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_OPEN_POSITIONS` | 5 | Maximum concurrent open positions |

This prevents overexposure and concentration risk.

### 6. Risk/Reward Ratio

**Check:** Is the trade's risk/reward ratio at least 1.2?

```
Risk/Reward = |Take Profit - Entry| / |Entry - Stop Loss|
```

Trades with a risk/reward below 1.2 are rejected regardless of confidence.

### 7. Confidence Threshold

**Check:** Is the AI's confidence score at least 0.60?

Low-confidence ideas are filtered at the analyzer level (never generated), but this check acts as a second gate in case of edge cases.

### 8. Correlation / Concentration

**Check:** Does opening this position exceed the max-per-group limit in the same correlation group (e.g., MEME, ALT_L1, DeFi)?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_CORRELATION_PER_GROUP` | 2 | Maximum positions in one correlation group |

### 9. Consecutive Loss Streak

**Check:** Are there 3 or more consecutive losses?

Rejects trades when the system is on a losing streak. At 5 consecutive losses, the circuit breaker trips.

### 10. Entry Price Sanity

**Check:** Is the entry price positive and non-zero?

Guards against data errors producing invalid trade parameters.

### 11. Stop-Loss Required

**Check:** Is a valid stop-loss set, and does it differ from the entry price?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `REQUIRE_STOP_LOSS` | true | Whether stop-loss is mandatory |

### 12. Stale Data Guard

**Check:** Is the trade idea less than 5 minutes old?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `STALE_DATA_MAX_AGE_SEC` | 300 | Maximum age of trade idea in seconds |

### 13. Cooldown After Loss

**Check:** Has the cooldown period elapsed since the last losing trade?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `COOLDOWN_AFTER_LOSS_SEC` | 300 | Seconds to wait after a loss |

### 14. Portfolio Exposure Limit

**Check:** Would total open exposure (existing + new) exceed the portfolio exposure cap?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_PORTFOLIO_EXPOSURE_PCT` | 80% | Maximum total portfolio exposure |

### 15. Per-Symbol Exposure Limit

**Check:** Would this asset's total exposure exceed the per-symbol cap?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_SYMBOL_EXPOSURE_PCT` | 20% | Maximum exposure to a single asset |

### 16. Volatility Guard

**Check:** Is the ATR (as % of price) below the volatility threshold?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `VOLATILITY_GUARD_ATR_PCT` | 6.0% | Maximum ATR-to-price ratio |

Rejects trades during extreme volatility conditions where stops are unreliable. **When ATR data is unavailable, the volatility guard fails closed and rejects the trade.** This ensures the system never enters a position without a valid volatility assessment.

Of the 18 pre-trade checks, **17 are fail-closed** (including the volatility guard) and **1 is fail-open** (the liquidity guard only, which is skipped when order book data is unavailable).

### 17. Liquidity Guard

**Check:** Is there sufficient order book depth to fill the proposed position without excessive slippage?

This check runs on live order-flow data when available (fail-open -- skipped if order book data is unavailable). It examines cumulative depth within a configurable price band and rejects trades where the order book cannot absorb the proposed position size. The liquidity guard is the **only** fail-open check in the system.

### 18. Macro Event Gate

**Check:** Is a major macroeconomic event imminent or in progress?

| State | Window | Effect |
|-------|--------|--------|
| `NORMAL` | >24h from any event | No restriction |
| `PRE_EVENT_CAUTION` | Within 24h before event | Logged warning (informational) |
| `EVENT_LOCKDOWN` | 30min before to 30min after | **Trade rejected** |
| `POST_EVENT_VOLATILITY` | 30min to 4h after | Logged warning (informational) |
| `BLACKOUT` | Calendar evaluation failed | **Trade rejected** (fail-closed) |

Tracked events: FOMC decisions, CPI, Core PCE, NFP, PPI, GDP, ISM PMI, Retail Sales, Jobless Claims, Fed speeches. The macro calendar includes the full **2026 schedule** for FOMC, CPI, NFP, and PCE events, and uses a fail-closed design -- if the calendar cannot be evaluated, the state defaults to `BLACKOUT` and all trades are blocked.

## Circuit Breaker

The circuit breaker is a safety mechanism that halts all trading when risk limits are breached.

**Trigger conditions:**
- Daily loss exceeds `MAX_DAILY_LOSS_PCT` (default: 5%)
- Portfolio drawdown exceeds `MAX_DRAWDOWN_PCT` (default: 10%)

**Behavior when active:**
- All new trade ideas are automatically rejected (Check #1 fails).
- The Telegram `/risk` command shows "Circuit Breaker: ACTIVE".
- The `/status` command shows "Circuit Breaker: TRIPPED".

**Reset:**
- The circuit breaker can only be reset manually (requires code-level or authorized command).
- This is intentional -- automatic reset would defeat the purpose.
- The reset is logged as an audit event.

## Backtest State Isolation

The `BacktestEngine` creates **isolated temporary state files** for portfolio and circuit breaker state during backtest runs. This ensures that backtests never pollute the production circuit breaker or portfolio state. Each backtest operates in its own sandbox -- tripping the circuit breaker during a backtest replay has no effect on the live or paper trading state. Temporary files are cleaned up automatically when the backtest completes.

## Re-Check on Confirmation

When a human taps "Confirm" on a pending trade idea, the risk engine runs all 18 checks again against the current portfolio state. This catches scenarios where:

- Another trade was confirmed between idea generation and confirmation.
- Market movement changed the risk profile.
- The daily loss limit was reached by another closed position.

If the re-check fails, the confirmation is rejected with an explanation.

**Limitation:** The re-check uses the original entry price and stored ATR, not live market prices. It catches portfolio-state drift (new positions, drawdown changes, daily PnL updates) but not price drift on the asset itself. The stale data guard (check #12) partially mitigates this by rejecting ideas older than 5 minutes.

## Position Sizing

Position size uses fixed-fractional risk sizing based on stop distance:

```
risk_budget = equity * (MAX_POSITION_PCT / 100)   # e.g. 2% = max dollar loss
position_usd = risk_budget / stop_distance_pct
position_usd = min(position_usd, equity * 0.20)   # capped at 20% notional
```

With default settings ($10,000 equity, 2% risk budget, 2.5% stop distance via 2.5x ATR):
- Risk budget: $200 (max dollar loss if stopped out)
- Uncapped position: $200 / 0.025 = $8,000
- Capped position: min($8,000, $2,000) = **$2,000** (20% of equity)
- Actual dollar risk at 2.5% stop: $2,000 × 0.025 = $50 (well below the $200 budget)

**Important:** `MAX_POSITION_PCT` (2%) is a *risk budget* (max loss per trade), not a position size cap. The actual position size is determined by stop distance and capped at `MAX_SYMBOL_EXPOSURE_PCT` (20%). With tight stops, the risk budget implies positions larger than 20%, so the notional cap binds and the trade risks less than the full 2% budget.

## Correlation Check

The `MAX_CORRELATION_PER_GROUP` parameter (default: 2) prevents concentrated bets in the same correlation group. Assets are mapped to groups (BTC, ETH, ALT_L1, MEME, DEFI, L2, AI, CEX). If you already have 2 positions in the MEME group, a third MEME position is rejected.

## Why Simulation-First

RUNECLAW defaults to paper trading for several reasons:

1. **Hackathon safety.** Judges can evaluate the system without real financial risk.
2. **Iterative development.** The strategy can be tested and refined before any capital is at risk.
3. **Regulatory caution.** Automated trading with real funds may have legal implications depending on jurisdiction.
4. **Trust building.** A system should prove itself in simulation before handling real money.

To enable live trading (not recommended for hackathon use):

```bash
SIMULATION_MODE=false
LIVE_TRADING_ENABLED=true
```

Both flags must be set. This two-key mechanism prevents accidental activation.

## Compliance Considerations

RUNECLAW is a hackathon project and is not designed for production use with real funds. However, the architecture supports compliance-friendly patterns:

- **Full audit trail.** Every decision is logged with timestamp, action, reasoning, and result.
- **Human-in-the-loop.** No automated execution without explicit confirmation.
- **Rate limiting.** Telegram commands are rate-limited per user.
- **Immutable records.** Trade executions use Pydantic models that prevent post-hoc mutation.
- **Fail-closed defaults.** The system starts in the safest possible state.

For production deployment, additional measures would be needed: KYC integration, regulatory reporting, segregated accounts, and third-party risk audits.
