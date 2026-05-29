# Paper Trading

RUNECLAW ships with a fully functional paper trading system. All trades execute against a simulated portfolio with no real money at risk.

## How It Works

The `PortfolioTracker` maintains an in-memory ledger that tracks:

- **Cash balance** -- starts at $10,000 (configurable via `PAPER_BALANCE_USD`)
- **Open positions** -- active trades with entry price, quantity, SL, and TP
- **Trade history** -- closed trades with realized PnL
- **Daily PnL** -- cumulative profit/loss for the current day
- **Peak equity** -- highest equity value seen (used for drawdown calculation)

## Opening a Position

When a trade idea is confirmed:

1. The risk engine re-checks all seven gates.
2. Position size is calculated: `equity * MAX_POSITION_PCT / 100`.
3. Quantity is derived: `position_usd / entry_price`.
4. The position is recorded with entry price, direction, SL, and TP.
5. Cash balance is reduced by the position size.

## Closing a Position

Positions close in two ways:

### Automatic (Stop-Loss / Take-Profit)

The engine's monitor loop continuously checks open positions against current market prices:

- **Long position:** SL hit if price <= stop_loss; TP hit if price >= take_profit.
- **Short position:** SL hit if price >= stop_loss; TP hit if price <= take_profit.

When triggered, the position closes at the current market price and PnL is calculated.

### Manual

Positions can also be closed through the skill system (future enhancement).

## PnL Calculation

```
LONG:  PnL = (exit_price - entry_price) * quantity
SHORT: PnL = (entry_price - exit_price) * quantity
```

After closing:
- Cash balance is restored: `balance += position_size + pnl`
- Daily PnL is updated
- Peak equity is recalculated

## Portfolio Snapshot

The `/portfolio` command returns a `PortfolioState` object:

| Field | Description |
|-------|-------------|
| `balance_usd` | Available cash (not locked in positions) |
| `equity_usd` | Cash + value of open positions |
| `open_positions` | Number of active positions |
| `total_trades` | Total closed trades |
| `win_rate` | Percentage of profitable closed trades |
| `total_pnl` | Cumulative realized PnL |
| `daily_pnl` | Today's realized PnL |
| `max_drawdown_pct` | Current drawdown from peak equity |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_BALANCE_USD` | 10,000 | Starting paper balance |
| `SIMULATION_MODE` | true | Must be true for paper trading |
| `MAX_POSITION_PCT` | 2.0 | Max position size as % of equity |
| `MAX_OPEN_POSITIONS` | 5 | Maximum concurrent positions |

## Limitations

The paper trading system is designed for demonstration purposes:

- **In-memory only.** Portfolio state is lost on restart. A production version would persist to a database.
- **No slippage model.** Trades execute at the exact signal price. Real markets have slippage.
- **No fees.** Trading fees are not deducted. Bitget's fee structure would need to be integrated for accurate backtesting.
- **No partial fills.** All orders are fully filled instantly.

These simplifications are appropriate for a hackathon demo but would need to be addressed for real-world use.

## Example Session

```
runeclaw> get_portfolio
Balance: $10,000.00
Equity: $10,000.00
Open: 0 | Total: 0
Win Rate: 0%
Total PnL: $0.00

runeclaw> analyze_asset BTC
Trade Idea [TI-a1b2c3d4]
LONG BTC/USDT
Entry: $67,432.50
SL: $66,580.00 | TP: $68,710.00
Confidence: 72%
R:R = 1.50

runeclaw> execute_paper_trade trade_id=TI-a1b2c3d4
Executed paper LONG BTC/USDT ($200.00)

runeclaw> get_portfolio
Balance: $9,800.00
Equity: $10,000.00
Open: 1 | Total: 0
Win Rate: 0%
Total PnL: $0.00
```
