# MCP Integration

RUNECLAW is designed as a **standalone trading agent** but its architecture maps naturally to the **Model Context Protocol (MCP)** used by the Bitget Agent Hub.

---

## What is MCP?

The Model Context Protocol is a standard interface that allows AI agents to expose their capabilities as structured tools. An MCP-compatible agent publishes a set of tools (functions) that other systems -- including the Bitget Agent Hub -- can discover and invoke.

Each tool has:
- A **name** (e.g. `runeclaw_scan`)
- A **description** (what it does)
- A **schema** (what inputs it accepts)
- A **handler** (the function that executes it)

---

## RUNECLAW Tool Map

RUNECLAW's internal skill registry maps directly to MCP tools. Each skill is a self-contained async function that takes structured input and returns structured output via Pydantic models.

| MCP Tool | Internal Skill | Description |
|----------|---------------|-------------|
| `runeclaw_scan` | `scan_market` | Scan Bitget markets for volume spikes and momentum signals |
| `runeclaw_analyze` | `analyze_asset` | Run AI + technical analysis on a specific asset, generate trade idea |
| `runeclaw_risk` | `check_risk` | Evaluate current risk status, circuit breaker, exposure limits |
| `runeclaw_execute` | `execute_paper_trade` | Execute a confirmed paper trade |
| `runeclaw_portfolio` | `get_portfolio` | Return current portfolio state (balance, equity, positions, PnL) |
| `runeclaw_explain` | `explain_trade` | Return full decision chain for a specific trade idea |
| `runeclaw_macro` | `macro_calendar` | Return macro event calendar and current risk state |
| `runeclaw_backtest` | `run_backtest` | Run backtest with synthetic data and return metrics |

---

## Architecture Alignment

RUNECLAW's skill registry pattern was designed with MCP compatibility in mind:

```text
MCP Client (Agent Hub / External AI)
        |
        v
  MCP Tool Layer  ← thin adapter, maps tool calls to skills
        |
        v
  Skill Registry  ← existing RUNECLAW skill system
        |
        v
  RuneClaw Engine ← orchestrator with full state
        |
    ┌───┼───┐
    v   v   v
Scanner Analyzer Risk Engine
```

The MCP adapter is a thin translation layer. It does not add business logic -- it maps MCP tool calls to the existing `BaseSkill.execute()` interface and serializes Pydantic responses back to the caller.

### Skill Interface

Every RUNECLAW skill follows this contract:

```python
class BaseSkill(ABC):
    name: str = "unnamed"
    description: str = ""

    @abstractmethod
    async def execute(self, engine: RuneClawEngine, **kwargs) -> str:
        ...
```

MCP tools call `skill.execute(engine, **params)` and return the string result. Input validation happens via Pydantic at the engine boundary.

---

## Data Flow

```text
MCP Request: { "tool": "runeclaw_analyze", "input": { "symbol": "BTC/USDT" } }
                  |
                  v
         AnalyzeAssetSkill.execute(engine, symbol="BTC/USDT")
                  |
                  v
         Engine: fetch candles → compute indicators → LLM thesis → TradeIdea
                  |
                  v
         RiskEngine: 18 fail-closed checks
                  |
                  v
         MCP Response: { "result": "LONG BTC/USDT | Confidence 72% | R:R 2.8 | ..." }
```

All inputs are validated. All outputs are structured. The risk gate runs on every analysis regardless of whether the call comes from Telegram, CLI, or MCP.

---

## Integration Status

| Component | Status |
|-----------|--------|
| Skill registry (internal) | Implemented -- 12 skills registered |
| Pydantic schemas at all boundaries | Implemented |
| Async execution model | Implemented |
| MCP tool adapter layer | Planned -- architecture ready, adapter not yet written |
| Bitget Agent Hub registration | Planned -- pending Agent Hub availability |

The MCP adapter is not yet implemented as production code. The skill registry is the integration surface -- when Agent Hub tooling is available, wrapping each skill as an MCP tool requires only the adapter layer, no changes to core logic.

---

## Future: Agent Hub Registration

When the Bitget Agent Hub supports MCP tool registration, RUNECLAW will:

1. Expose all 12 skills as MCP tools with JSON Schema input/output definitions
2. Enforce the same 18-check risk gate on all tool invocations
3. Require human confirmation for any trade execution (even via MCP)
4. Log all MCP calls through the existing structured audit system

The fail-closed and human-in-the-loop guarantees apply regardless of interface. An MCP call cannot bypass the risk engine or skip confirmation.
