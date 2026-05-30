# Agent Hub Alignment

RUNECLAW is built to align with the **Bitget Agent Hub** ecosystem. This page maps RUNECLAW's capabilities against the Agent Hub's core domains and shows how the architecture connects to GetClaw's autonomous trading vision.

---

## GetClaw Capability Matrix

GetClaw is positioned as a zero-install autonomous AI trading agent. The table below maps each GetClaw capability to RUNECLAW's implementation status.

| GetClaw Capability | RUNECLAW Implementation | Status |
|---|---|---|
| **Market activity monitoring** | MarketScanner: volume spike detection, momentum scoring, top-N mover ranking across all Bitget USDT pairs | Implemented |
| **Portfolio exposure tracking** | PortfolioTracker: real-time equity, per-symbol exposure, total exposure %, drawdown from peak, daily PnL | Implemented |
| **Funding rate awareness** | Not yet implemented -- planned for futures integration via ccxt `fetchFundingRate()` | Roadmap |
| **Volatility shift detection** | ADX-14 regime detection (TREND_UP/DOWN/RANGE/CHOP) + ATR-based volatility guard + adaptive SL/TP scaling | Implemented |
| **Liquidation risk monitoring** | Circuit breaker (5% daily loss, 10% drawdown), per-symbol exposure caps (20%), max position count | Implemented |
| **Macro development awareness** | MacroCalendar: 10 event types (FOMC, CPI, NFP, PCE, PPI, GDP, ISM, Retail Sales, Jobless Claims, Fed Speech), 5-state risk machine | Implemented |
| **Crypto narrative tracking** | LLM thesis generation incorporates market context; rule-based fallback when LLM unavailable | Partial |
| **Autonomous decision loop** | 9-state FSM: IDLE → SCANNING → ANALYZING → RISK_CHECK → CONFIRMING → EXECUTING → MONITORING → COOLING_DOWN / HALTED | Implemented |

---

## Agent Hub Domain Mapping

The Bitget Agent Hub organizes capabilities across several domains. Here is how RUNECLAW maps to each.

### MCP Tools

RUNECLAW exposes 12 internal skills that map directly to MCP tools. See [MCP Integration](mcp-integration.md) for the full tool map and data flow.

| Agent Hub Domain | RUNECLAW Tools |
|---|---|
| Market data | `runeclaw_scan` -- fetch tickers, volume spikes, momentum signals |
| Trading execution | `runeclaw_execute` -- paper trade execution (live via ccxt when enabled) |
| Portfolio management | `runeclaw_portfolio` -- balance, equity, positions, PnL, drawdown |
| Risk management | `runeclaw_risk` -- 18-check status, circuit breaker, exposure limits |
| Analysis | `runeclaw_analyze` -- technical indicators + LLM thesis + trade idea |
| Explainability | `runeclaw_explain` -- full decision chain for any trade idea |

### REST / WebSocket APIs

| Layer | Implementation |
|---|---|
| Exchange REST | Bitget Spot V2 API via ccxt (`fetchTickers`, `fetchOHLCV`, `createOrder`) |
| Exchange WebSocket | Not yet implemented -- planned for real-time price streaming and order book |
| Internal API | Async Python methods on `RuneClawEngine` -- all Pydantic-validated |

### Skills & CLI

| Interface | Details |
|---|---|
| Skill registry | 12 registered skills, extensible via `BaseSkill` subclass |
| Telegram bot | 18 slash commands with inline keyboard confirmation |
| CLI mode | Direct skill invocation via `python -m bot.main --mode cli` |
| Scan mode | One-shot market scan: `python -m bot.main --mode scan` |

### Developer Ecosystem

| Asset | Location |
|---|---|
| Source code | [GitHub](https://github.com/Humanoid-Traders/RUNECLAW) -- MIT license |
| Documentation | [GitBook](https://humanoid-traders-1.gitbook.io/humanoid-traders-ai) |
| Test suite | 315+ unit tests (`pytest tests/test_core.py -v`) |
| Demo data | `demo/sample_output.json`, `demo/sample_risk_check.json`, `demo/sample_portfolio.json` |
| Backtest engine | Synthetic data with GBM + GARCH, intrabar SL/TP/trailing stop simulation |

---

## Architecture Alignment with GetClaw

```text
┌─────────────────────────────────────────────────────────┐
│                  BITGET AGENT HUB                       │
│                                                         │
│  MCP Layer ←──── RUNECLAW Tool Adapter (planned)        │
│     │                                                   │
│     v                                                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │              RUNECLAW ENGINE                      │   │
│  │                                                   │   │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │   │
│  │  │ Scanner  │  │ Analyzer │  │ Risk Engine   │  │   │
│  │  │ (Bitget) │  │ (LLM+TA) │  │ (18 checks)  │  │   │
│  │  └────┬─────┘  └────┬─────┘  └──────┬────────┘  │   │
│  │       │              │               │           │   │
│  │       v              v               v           │   │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────────┐  │   │
│  │  │ Macro    │  │ Order    │  │ Portfolio     │  │   │
│  │  │ Calendar │  │ Flow     │  │ Tracker       │  │   │
│  │  └──────────┘  └──────────┘  └───────────────┘  │   │
│  │                                                   │   │
│  │  FSM: IDLE → SCAN → ANALYZE → RISK → CONFIRM     │   │
│  │       → EXECUTE → MONITOR → COOL_DOWN / HALTED   │   │
│  └──────────────────────────────────────────────────┘   │
│                                                         │
│  Interfaces: Telegram Bot │ CLI │ MCP (planned)         │
│  Audit: trade.jsonl │ risk.jsonl │ system.jsonl          │
└─────────────────────────────────────────────────────────┘
```

---

## Roadmap: Closing the Gaps

| Capability | Current | Target | Priority |
|---|---|---|---|
| MCP tool adapter | Architecture ready, adapter not written | Full MCP tool registration on Agent Hub | High |
| WebSocket streaming | Polling via REST | Real-time price + order book via Bitget WS | High |
| Funding rate monitor | Not implemented | Fetch and display funding rates, alert on extremes | Medium |
| Narrative engine | LLM thesis only | Structured crypto narrative tracking with news feed | Medium |
| bgc CLI integration | Not implemented | `bgc runeclaw scan`, `bgc runeclaw analyze BTC` | Low |
| Multi-exchange | Bitget only | Abstract exchange layer for ccxt-supported exchanges | Low |

---

## Hackathon Track Alignment

| Track | Alignment |
|---|---|
| **Track 1: Trading Agent** | Primary. Full autonomous trading pipeline: scan → analyze → risk → confirm → execute → monitor. |
| **Track 2: Trading Infra** | Secondary. Modular skill registry, structured audit logging, fail-closed risk engine, extensible architecture. |
