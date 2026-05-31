# API Reference

This document covers the core data models and programmatic interfaces in RUNECLAW. All models use Pydantic v2 with strict validation.

## Data Models

All models are defined in `bot/utils/models.py`.

### MarketSignal

Emitted by the market scanner for each detected opportunity.

```python
class MarketSignal(BaseModel):
    symbol: str                    # e.g. "BTC/USDT"
    price: float                   # Current price
    change_pct_24h: float          # 24-hour price change %
    volume_usd_24h: float          # 24-hour volume in USD
    volume_spike: bool = False     # True if volume > 2x rolling avg
    momentum_score: float          # [-1.0, 1.0] momentum heuristic
    timestamp: datetime
```

### TradeIdea

A fully-formed trade thesis produced by the AI analyzer.

```python
class TradeIdea(BaseModel):
    id: str                        # e.g. "TI-a1b2c3d4"
    asset: str                     # e.g. "BTC/USDT"
    direction: Direction           # LONG or SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float              # [0.0, 1.0]
    reasoning: str                 # LLM or rule-based explanation
    signals_used: list[str]        # Indicator names used
    timestamp: datetime
```

**Computed property:**
```python
@property
def risk_reward_ratio(self) -> float:
    risk = abs(entry_price - stop_loss)
    reward = abs(take_profit - entry_price)
    return round(reward / risk, 2)
```

### RiskCheck

Result of the pre-trade risk evaluation.

```python
class RiskCheck(BaseModel):
    trade_id: str
    verdict: RiskVerdict           # APPROVED or REJECTED
    position_size_usd: float
    position_pct: float
    daily_loss_pct: float
    drawdown_pct: float
    checks_passed: list[str]
    checks_failed: list[str]
    reason: str
    timestamp: datetime
```

### TradeExecution

Immutable record of an executed trade (paper or live).

```python
class TradeExecution(BaseModel):
    trade_id: str
    asset: str
    direction: Direction           # LONG or SHORT
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    status: TradeStatus            # PENDING | CONFIRMED | EXECUTED | CANCELLED | REJECTED
    pnl: float = 0.0
    exit_price: float | None
    is_paper: bool = True
    opened_at: datetime
    closed_at: datetime | None
```

### PortfolioState

Point-in-time snapshot of the portfolio.

```python
class PortfolioState(BaseModel):
    balance_usd: float
    equity_usd: float
    open_positions: int
    total_trades: int
    win_rate: float
    total_pnl: float
    daily_pnl: float
    max_drawdown_pct: float
    timestamp: datetime
```

## Enums

```python
class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

class RiskVerdict(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"

class TradeStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
```

## Engine API

The `RuneClawEngine` class is the central orchestrator.

### Methods

| Method | Returns | Description |
|--------|---------|-------------|
| `run()` | `None` | Start the continuous scan-analyze-monitor loop |
| `stop()` | `None` | Stop the engine and close exchange connections |
| `confirm_trade(trade_id)` | `str` | Confirm a pending trade (re-checks risk) |
| `reject_trade(trade_id)` | `str` | Reject a pending trade idea |

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `pending_ideas` | `list[TradeIdea]` | Currently pending trade ideas |
| `portfolio` | `PortfolioTracker` | Portfolio tracker instance |
| `risk` | `RiskEngine` | Risk engine instance |
| `mode` | `EngineMode` | Current pipeline stage |

## Skill Interface

All skills implement the `BaseSkill` abstract class:

```python
class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
        """Run the skill and return a human-readable result string."""
```

### Registering a Custom Skill

```python
from bot.skills.skill_registry import BaseSkill, SkillRegistry

class MySkill(BaseSkill):
    name = "my_skill"
    description = "Does something useful"

    async def execute(self, engine, **kwargs):
        return "Result"

registry = SkillRegistry()
registry.register(MySkill())
```

## Audit Logger

The `audit()` function writes structured JSON to the appropriate channel:

```python
from bot.utils.logger import audit, trade_log

audit(
    trade_log,
    "Trade idea generated",
    action="analyze",
    reasoning="RSI oversold + volume spike",
    result="BUY BTC",
    data={"confidence": 0.82}
)
```

### Log Entry Format

```json
{
  "ts": "2026-05-15T14:30:00.000Z",
  "level": "INFO",
  "channel": "runeclaw.trade",
  "message": "Trade idea generated",
  "action": "analyze",
  "reasoning": "RSI oversold + volume spike",
  "result": "BUY BTC",
  "data": {"confidence": 0.82}
}
```

### Log Channels

| Channel | Logger | File |
|---------|--------|------|
| Trade | `trade_log` | `logs/trade.jsonl` |
| Risk | `risk_log` | `logs/risk.jsonl` |
| System | `system_log` | `logs/system.jsonl` |
