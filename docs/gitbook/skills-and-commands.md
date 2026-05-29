# Skills & Commands

RUNECLAW uses a modular skill system. Every capability is registered as a self-contained skill that can be invoked by the Telegram bot or the CLI.

## Telegram Commands

| Command | Skill | Description |
|---------|-------|-------------|
| `/scan` | `scan_market` | Scan the Bitget exchange for top movers and volume spikes |
| `/analyze <SYMBOL>` | `analyze_asset` | Run AI + technical analysis on a specific asset |
| `/portfolio` | `get_portfolio` | Display paper portfolio summary |
| `/trade` | -- | View pending trade ideas with confirm/reject buttons |
| `/risk` | `check_risk` | Show risk metrics and circuit breaker status |
| `/status` | -- | Bot mode, engine state, equity snapshot |
| `/help` | -- | List all available commands |

## Command Details

### /scan

Fetches all USDT-pair tickers from Bitget, filters by volume, scores by momentum, and returns the top 5 movers.

**Example output:**
```
Top movers:
BTC/USDT: $67,432.50 (+3.2%) SPIKE
ETH/USDT: $3,891.20 (+2.8%)
SOL/USDT: $178.45 (+5.1%) SPIKE
DOGE/USDT: $0.1823 (-1.4%)
AVAX/USDT: $42.67 (+1.9%)
```

### /analyze BTC

Runs the full analysis pipeline on a single asset:

1. Fetches current ticker data
2. Retrieves 100 hourly candles
3. Computes RSI, MACD, Bollinger Bands, ATR
4. Generates a directional thesis via LLM (or rule-based fallback)
5. Creates a `TradeIdea` with entry, SL, TP, and confidence

If the idea passes risk checks, it appears with inline **Confirm** / **Reject** buttons.

**Example output:**
```
Trade Idea [TI-a1b2c3d4]
LONG BTC/USDT
Entry: $67,432.50
SL: $66,580.00 | TP: $68,710.00
Confidence: 72%
R:R = 1.50
Reasoning: RSI at 38 suggests oversold, MACD crossing bullish, volume spike detected.
```

### /portfolio

Displays the current paper portfolio state.

**Example output:**
```
Balance: $9,800.00
Equity: $10,150.00
Open: 2 | Total: 5
Win Rate: 60%
Total PnL: $150.00
```

### /trade

Lists all pending trade ideas awaiting human confirmation. Each idea is displayed with:

- Direction (LONG/SHORT) and asset
- Confidence percentage
- Risk/reward ratio
- Inline keyboard: Confirm / Reject

### /risk

Shows the current risk status.

**Example output:**
```
Equity: $10,150.00
Daily PnL: $75.00
Drawdown: 1.2%
Circuit Breaker: OK
```

### /status

Quick dashboard showing:

- Mode: SIMULATION or LIVE
- Engine state: SCAN, ANALYZE, TRADE, or MONITOR
- Circuit breaker: OK or TRIPPED
- Current equity
- Number of open positions

## Inline Keyboard (Trade Confirmation)

When a trade idea is generated, the bot sends a message with two buttons:

- **Confirm** -- Triggers `engine.confirm_trade(trade_id)`. Risk is re-checked. If still approved, the paper trade executes.
- **Reject** -- Triggers `engine.reject_trade(trade_id)`. The idea is discarded and logged.

## CLI Skills

In CLI mode (`python -m bot.main --mode cli`), you can invoke skills directly:

```
runeclaw> scan_market
runeclaw> analyze_asset BTC
runeclaw> get_portfolio
runeclaw> check_risk
runeclaw> execute_paper_trade trade_id=TI-a1b2c3d4
runeclaw> explain_trade trade_id=TI-a1b2c3d4
```

## Skill Registry

All skills extend the `BaseSkill` abstract class:

```python
class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
        ...
```

Built-in skills are registered automatically via `build_default_registry()`:

| Skill Name | Class |
|------------|-------|
| `scan_market` | `ScanMarketSkill` |
| `analyze_asset` | `AnalyzeAssetSkill` |
| `check_risk` | `CheckRiskSkill` |
| `execute_paper_trade` | `ExecutePaperTradeSkill` |
| `get_portfolio` | `GetPortfolioSkill` |
| `explain_trade` | `ExplainTradeSkill` |

Custom skills can be added by subclassing `BaseSkill` and calling `registry.register(MySkill())`.
