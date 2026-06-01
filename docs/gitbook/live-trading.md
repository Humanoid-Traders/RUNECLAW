# Enabling Live Trading

RUNECLAW ships in **simulation mode by default**. Paper trading is the default. No real funds are at risk unless you explicitly enable live trading through a two-key unlock mechanism.

> **Warning:** Live trading with real funds carries significant financial risk. RUNECLAW is a hackathon prototype and has not been audited for production use. Enable live mode only if you fully understand the risks, have tested extensively in paper mode, and accept responsibility for any losses.

---

## Safety Architecture

Two independent flags must both be set before any real order is sent to the exchange:

| Flag | Default | Purpose |
|------|---------|---------|
| `SIMULATION_MODE` | `true` | Master simulation switch. When true, all trades are paper-only. |
| `LIVE_TRADING_ENABLED` | `false` | Secondary confirmation. Must be explicitly set to `true`. |

Both conditions must be met for live execution:

```text
SIMULATION_MODE=false  AND  LIVE_TRADING_ENABLED=true  →  Live orders sent to Bitget
```

Any other combination defaults to paper trading:

| `SIMULATION_MODE` | `LIVE_TRADING_ENABLED` | Result |
|---|---|---|
| `true` | `false` | Paper trading (default) |
| `true` | `true` | Paper trading |
| `false` | `false` | Paper trading |
| `false` | `true` | **Live trading** |

This two-key mechanism prevents accidental activation. A single misconfigured flag cannot enable live trading.

---

## Step-by-Step Activation

### 1. Paper trade first

Run RUNECLAW in default mode for an extended period. Verify:

- Trade ideas are sensible and match your risk tolerance
- Risk checks are firing correctly
- Win rate, drawdown, and PnL are within acceptable bounds
- The macro calendar is blocking trades during high-impact events

### 2. Configure Bitget API credentials

```bash
# .env
BITGET_API_KEY=your_api_key
BITGET_API_SECRET=your_api_secret
BITGET_PASSPHRASE=your_passphrase
BITGET_SANDBOX=false
```

Use **read-only API keys** first to confirm market data works. Only enable trade permissions when you are ready.

### 3. Set risk limits

Review and tighten risk parameters before going live:

```bash
# .env — conservative live settings
MAX_POSITION_PCT=1.0          # 1% risk budget per trade (down from 2%)
MAX_DAILY_LOSS_PCT=3.0        # 3% daily loss cap (down from 5%)
MAX_DRAWDOWN_PCT=5.0          # 5% max drawdown (down from 10%)
MAX_OPEN_POSITIONS=3           # 3 concurrent positions (down from 5)
MAX_PORTFOLIO_EXPOSURE_PCT=50.0  # 50% max exposure (down from 80%)
```

### 4. Enable live mode

```bash
# .env — the two-key unlock
SIMULATION_MODE=false
LIVE_TRADING_ENABLED=true
```

### 5. Monitor closely

- Watch the Telegram bot for trade proposals
- Confirm or reject each trade manually
- Use `/risk` and `/status` to monitor exposure
- Use `/halt` immediately if anything looks wrong

---

## Safeguards That Remain Active in Live Mode

All safety mechanisms apply equally in live mode:

| Safeguard | Behavior |
|-----------|----------|
| 20-check risk gate | Every trade must pass all checks. No exceptions. |
| Human confirmation | Every trade requires Telegram approval before execution. |
| Circuit breaker | Auto-halts on daily loss or drawdown breach. |
| Cooldown timer | Blocks trading after consecutive losses. |
| Stale data guard | Rejects ideas older than 5 minutes. |
| Macro event gate | Blocks trades during FOMC, CPI, NFP, etc. |
| Volatility guard | Rejects trades during extreme ATR conditions. |
| Audit logging | Every decision is recorded in structured JSON. |

---

## Emergency Shutdown

If anything goes wrong in live mode:

1. Send `/halt` via Telegram -- trips the circuit breaker, cancels all pending ideas
2. Set `SIMULATION_MODE=true` in `.env` and restart
3. Revoke API trade permissions on the Bitget dashboard
4. Review `logs/trade.jsonl` and `logs/risk.jsonl` for post-mortem

The circuit breaker requires manual reset (`/reset` command) after being tripped. This is intentional -- automatic recovery would defeat the safety purpose.

---

## Reverting to Paper Mode

```bash
# .env
SIMULATION_MODE=true
LIVE_TRADING_ENABLED=false
```

Restart the bot. All trades revert to the paper portfolio. Open live positions on Bitget must be managed directly on the exchange.
