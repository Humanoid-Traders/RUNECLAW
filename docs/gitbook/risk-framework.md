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
2. **Risk engine level** -- 16 independent checks must all pass.
3. **Confirmation level** -- Risk is re-evaluated when the human confirms (the market may have moved).
4. **Configuration level** -- `SIMULATION_MODE=true` and `LIVE_TRADING_ENABLED=false` are both set by default. Live trading requires both flags to be flipped.

## The Sixteen Risk Checks

Every `TradeIdea` must pass all sixteen checks before it enters the pending queue.

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

**Check:** Has today's cumulative loss exceeded `MAX_DAILY_LOSS_PCT`?

| Parameter | Default | Description |
|-----------|---------|-------------|
| `MAX_DAILY_LOSS_PCT` | 5.0% | Maximum daily loss as % of balance |

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
| `VOLATILITY_GUARD_ATR_MULT` | 6.0% | Maximum ATR-to-price ratio |

Rejects trades during extreme volatility conditions where stops are unreliable.

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

## Re-Check on Confirmation

When a human taps "Confirm" on a pending trade idea, the risk engine runs all 16 checks again against the current portfolio state. This catches scenarios where:

- Another trade was confirmed between idea generation and confirmation.
- Market movement changed the risk profile.
- The daily loss limit was reached by another closed position.

If the re-check fails, the confirmation is rejected with an explanation.

## Position Sizing

Position size uses fixed-fractional risk sizing based on stop distance:

```
risk_budget = equity * (MAX_POSITION_PCT / 100)
position_usd = risk_budget / stop_distance_pct
position_usd = min(position_usd, equity * 0.20)  # capped at 20% notional
```

With default settings ($10,000 equity, 2% risk budget, 2% stop distance):
- Risk budget: $200 (max dollar loss if stopped out)
- Position size: $200 / 0.02 = $10,000 (but capped at 20% = $2,000)
- This ensures each trade risks the same dollar amount regardless of stop width.

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
