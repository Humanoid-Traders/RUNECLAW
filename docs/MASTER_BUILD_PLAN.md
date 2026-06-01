# RUNECLAW — Master Build Plan
## Humanoid Traders | Bitget AI Builder Base Camp

---

# 1. HACKATHON POSITIONING

## Executive Summary

Position RUNECLAW in the **AI Agent / Autonomous Trading** track as an **explainable, simulation-first autonomous trading command platform** — not as a bot, not as a dashboard, but as an **agent runtime with a risk-first architecture**.

## Primary Track: AI Trading Agent Infrastructure

**Why this wins:**

1. **Differentiation from the field.** Most hackathon submissions will be either (a) GPT wrappers that call exchange APIs, or (b) indicator dashboards with "AI" labels. RUNECLAW is neither. It is a structured agent loop with perception, scoring, risk gating, and auditable execution — the kind of system architecture that signals engineering maturity to judges.

2. **Alignment with Bitget Agent Hub.** The Agent Hub ecosystem wants modular, composable agents. RUNECLAW's skill-based architecture (scan, analyze, risk-check, execute, explain) maps directly to the agent-as-tool paradigm. This is not incidental — it is the core value proposition.

3. **Simulation-first is a feature, not a limitation.** In a regulatory environment where autonomous trading raises compliance questions, a system that defaults to paper trading and requires explicit human confirmation is *more* valuable than one that trades live. Judges know this. Position it as a design choice, not a constraint.

4. **Demo-ability.** A simulation-first system can be demonstrated live without risk, without API key exposure, and without waiting for market conditions. Every demo step is reproducible.

## Secondary Positioning: Developer Tools / Infrastructure

The same codebase supports positioning as developer infrastructure because:
- The skill registry is a plugin system other developers can extend
- The risk engine is decoupled and reusable
- The backtesting engine is a standalone utility
- The Telegram interface is a reference implementation, not the product itself

## Judge-Facing Product Definition

> RUNECLAW is an autonomous trading command platform that combines multi-timeframe market perception, AI-powered confluence scoring, and a fail-closed risk engine into a single agent runtime. It operates in simulation mode by default, requires human confirmation for every trade, and logs every decision in structured JSON for full auditability. Built for the Bitget ecosystem, RUNECLAW treats trading not as prediction, but as disciplined process execution — where the agent observes, scores, proposes, and the human decides.

---

# 2. PRODUCT DEFINITION

## Target Users

| Segment | Description | Primary Need |
|---------|-------------|-------------|
| **Quantitative retail traders** | Traders who think in systems, not tips | Structured process, not more signals |
| **Developer-traders** | Engineers who trade and want programmable infrastructure | Modular, extensible, API-first |
| **Risk-conscious algorithmic traders** | Traders who have been burned by black-box systems | Explainability, auditability, fail-closed defaults |
| **Hackathon judges** | Technical evaluators assessing innovation and execution | Architecture quality, demo clarity, safety awareness |

## Pain Points

1. **Signal overload without process.** Traders drown in indicators and alerts with no structured way to score, validate, and act on them.
2. **Black-box execution.** Existing bots execute without explaining why, making it impossible to improve or trust them.
3. **No risk separation.** Most trading tools mix signal generation with execution. There is no independent risk layer that can veto a trade.
4. **Backtesting-to-live gap.** Strategies that work in backtests fail live because the execution path is different. There is no unified pipeline.
5. **Audit failure.** When a trade goes wrong, there is no structured record of why the decision was made, what the alternatives were, or what the risk assessment said.

## Why This Matters Now

- **Bitget Agent Hub** is building an ecosystem for autonomous agents. The market needs reference implementations that demonstrate how to do this responsibly.
- **Regulatory scrutiny** of autonomous trading is increasing. Systems that can demonstrate explainability and human oversight have structural advantages.
- **LLM integration** in trading has moved past the hype phase. The question is no longer "can AI trade?" but "how do you build a system where AI assists trading decisions safely?"

## What Makes RUNECLAW AI-Native

RUNECLAW is not a dashboard with an AI button. The AI is the operating loop:

| Traditional Dashboard | RUNECLAW |
|----------------------|----------|
| Shows indicators, human decides | Agent scores confluence, proposes thesis, human confirms |
| Static alerts | Adaptive perception that adjusts to regime |
| No memory | Journal layer that learns from past trades |
| Execute or don't | Risk engine that independently validates every proposal |
| No explanation | Every decision includes structured reasoning |

The AI is not a feature. It is the architecture.

---

# 3. SYSTEM ARCHITECTURE

## Executive Summary

RUNECLAW is a **layered agent system** with strict separation between perception, decision, risk, and execution. Each layer is independently testable, replaceable, and auditable. The system defaults to simulation mode and requires explicit configuration changes plus human confirmation to affect any external state.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        FRONTEND LAYER                           │
│  Next.js Dashboard  ·  Telegram Bot  ·  CLI Interface           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket / REST
┌──────────────────────────▼──────────────────────────────────────┐
│                       API GATEWAY                                │
│  FastAPI  ·  Auth  ·  Rate Limiting  ·  Request Validation       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   AGENT ORCHESTRATOR                             │
│  Event Loop  ·  Mode Manager  ·  Skill Registry  ·  Scheduler   │
│                                                                  │
│  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────────────┐  │
│  │  SCAN   │→ │ ANALYZE  │→ │   RISK   │→ │ HUMAN CONFIRM   │  │
│  │  skill  │  │  skill   │  │  GATE    │  │ (blocking gate) │  │
│  └─────────┘  └──────────┘  └──────────┘  └────────┬────────┘  │
│                                                     │           │
│                                              ┌──────▼────────┐  │
│                                              │   EXECUTE     │  │
│                                              │   skill       │  │
│                                              └──────┬────────┘  │
│                                                     │           │
│                                              ┌──────▼────────┐  │
│                                              │   MONITOR     │  │
│                                              │   + AUDIT     │  │
│                                              └───────────────┘  │
└─────────────────────────────────────────────────────────────────┘
         │              │              │              │
┌────────▼───┐  ┌───────▼──────┐  ┌───▼────┐  ┌─────▼──────┐
│ PERCEPTION │  │   DECISION   │  │  RISK  │  │ EXECUTION  │
│   LAYER    │  │    ENGINE    │  │ ENGINE │  │  ADAPTER   │
│            │  │              │  │        │  │            │
│ · Scanner  │  │ · Confluence │  │ · Pre  │  │ · Paper    │
│ · OHLCV    │  │ · LLM thesis │  │ · Post │  │ · Backtest │
│ · Orderbook│  │ · Regime     │  │ · Circ │  │ · Dry-run  │
│ · Funding  │  │ · Hypothesis │  │   brkr │  │ · [Live]   │
│ · Sentiment│  │              │  │        │  │            │
└────────────┘  └──────────────┘  └────────┘  └────────────┘
         │              │              │              │
┌────────▼──────────────▼──────────────▼──────────────▼──────┐
│                     DATA / MEMORY LAYER                     │
│  Trade Journal  ·  Decision Log  ·  Risk Log  ·  Metrics    │
│  SQLite/JSON  ·  Structured Audit  ·  Performance Stats     │
└─────────────────────────────────────────────────────────────┘
         │
┌────────▼────────────────────────────────────────────────────┐
│                   OBSERVABILITY LAYER                        │
│  Structured Logging  ·  Metrics Export  ·  Health Checks     │
│  Decision Replay  ·  Trade Timeline  ·  Error Tracking       │
└─────────────────────────────────────────────────────────────┘
```

## Module Breakdown

### 3.1 Frontend Dashboard

| Attribute | Value |
|-----------|-------|
| **Purpose** | Visual command center for monitoring, review, and manual override |
| **Tech** | Next.js 14+ / Single HTML for MVP |
| **Key views** | Pipeline status, active positions, trade history, risk dashboard, decision log |
| **MVP** | Static landing page + Telegram as primary interface |
| **Ambitious** | Real-time WebSocket dashboard with trade replay |
| **Status** | Landing page complete. Dashboard is polish-phase. |

### 3.2 Backend API

| Attribute | Value |
|-----------|-------|
| **Purpose** | Unified interface between frontend(s) and agent core |
| **Tech** | FastAPI (Python) |
| **Endpoints** | `/scan`, `/analyze/{symbol}`, `/portfolio`, `/risk/status`, `/trades`, `/health` |
| **MVP** | Direct function calls from Telegram handler (current implementation) |
| **Ambitious** | Full REST API with WebSocket streaming, API key auth |
| **Note** | The current Telegram handler already implements the command interface. API is a refactor, not a rebuild. |

### 3.3 Agent Orchestrator

| Attribute | Value |
|-----------|-------|
| **Purpose** | Manages the agent loop lifecycle, mode transitions, and skill dispatch |
| **Tech** | Python async event loop |
| **Modes** | `SCAN` → `ANALYZE` → `TRADE` → `MONITOR` |
| **Key responsibility** | Ensuring the pipeline runs in order, no stage is skipped, and failures halt the pipeline |
| **Current** | `RuneClawEngine` class in `bot/core/engine.py` |

### 3.4 Perception Modules

| Module | Data Source | Output | MVP | Ambitious |
|--------|-----------|--------|-----|-----------|
| **Market Scanner** | Bitget REST API (tickers) | `MarketSignal[]` with momentum, volume | Implemented | Multi-exchange, funding rates |
| **OHLCV Provider** | Bitget candle API | Candlestick data for technicals | Implemented | Multi-timeframe fusion |
| **Orderbook Reader** | Bitget orderbook API | Bid/ask imbalance, liquidity zones | Mock data | Live WebSocket feed |
| **Funding Rate** | Bitget funding API | Funding rate signal | Mock data | Historical funding analysis |
| **Sentiment** | LLM-based news analysis | Sentiment score | LLM prompt | Social media feed integration |

### 3.5 Decision Engine

| Attribute | Value |
|-----------|-------|
| **Purpose** | Transform perception data into scored trade hypotheses |
| **Components** | Technical indicators (RSI, MACD, Bollinger), LLM thesis generator, confluence scorer, regime detector |
| **Output** | `TradeIdea` with direction, entry, SL, TP, confidence, reasoning |
| **Key design** | Every output includes structured reasoning. No black-box scores. |
| **Current** | `Analyzer` class in `bot/core/analyzer.py` |

### 3.6 Risk Engine

| Attribute | Value |
|-----------|-------|
| **Purpose** | Independent validation layer that can veto any trade proposal |
| **Design** | **Fail-closed.** If any check errors, trade is rejected. |
| **Checks** | Position size (2% risk budget), daily loss (5% max), drawdown (10% max), max positions (5), R:R ratio (≥1.2), confidence threshold (≥0.60), circuit breaker, correlation, stale data, volatility, macro events — 18 total |
| **Circuit breaker** | Auto-halts all trading when loss thresholds are breached |
| **Current** | `RiskEngine` class in `bot/risk/risk_engine.py` |
| **Critical property** | Risk engine is never bypassed. There is no override. |

### 3.7 Execution Adapter

| Mode | Behavior | Default |
|------|----------|---------|
| **Paper** | Simulated order fill at current price, tracked in local portfolio | **YES — default** |
| **Backtest** | Historical candle replay through same pipeline | Available |
| **Dry-run** | Full pipeline execution, logs intent, does not submit order | Available |
| **Live** | Submits order to exchange API | **DISABLED. Requires `SIMULATION_MODE=False` AND `LIVE_TRADING_ENABLED=True`** |

### 3.8 Memory / Journal Layer

| Component | Purpose | Format |
|-----------|---------|--------|
| **Trade Journal** | Records every executed trade with entry/exit, PnL, reasoning | JSON |
| **Decision Log** | Records every trade idea, including rejected ones, with risk check results | JSON |
| **Risk Log** | Records every risk evaluation, circuit breaker events | JSON |
| **Performance Stats** | Win rate, average R:R, max drawdown, Sharpe proxy | Computed from journal |

### 3.9 Backtesting Engine

| Attribute | Value |
|-----------|-------|
| **Purpose** | Replay historical data through the same agent pipeline |
| **Design** | Same perception → decision → risk → execution path, different data source |
| **MVP** | Feed historical OHLCV through analyzer, record paper trades |
| **Ambitious** | Multi-asset walk-forward optimization with regime-aware parameter tuning |
| **Key principle** | Backtest uses the same code path as live. No separate backtest logic. |

### 3.10 Observability / Telemetry

| Component | Purpose | MVP | Ambitious |
|-----------|---------|-----|-----------|
| **Structured logging** | JSON logs for every decision | Implemented | ELK/Grafana integration |
| **Health endpoint** | System status check | `/status` command | HTTP health check |
| **Decision replay** | Reconstruct why a trade was taken | JSON log review | Visual timeline |
| **Metrics export** | Performance metrics | Console output | Prometheus metrics |

---

# 4. AGENT LOOP

## Full Operating Loop

```
┌─────────────────────────────────────────────────────────────┐
│                    AGENT OPERATING LOOP                       │
│                                                              │
│  1. PERCEIVE                                                 │
│     ├─ Fetch market data (tickers, candles, orderbook)       │
│     ├─ Compute technical indicators (RSI, MACD, BB)          │
│     ├─ Detect regime (trend/range/chop)                      │
│     └─ Output: MarketSignal[] with scores                    │
│                          │                                   │
│  2. SCORE                │                                   │
│     ├─ Rank signals by confluence                            │
│     ├─ Filter by minimum thresholds                          │
│     └─ Output: Top N candidates                              │
│                          │                                   │
│  3. HYPOTHESIZE          │                                   │
│     ├─ Generate trade thesis via LLM + technicals            │
│     ├─ Define entry, stop-loss, take-profit                  │
│     ├─ Calculate position size                               │
│     └─ Output: TradeIdea with full reasoning                 │
│                          │                                   │
│  4. VALIDATE (RISK)      │                                   │
│     ├─ Run all 20 risk checks                                │
│     ├─ Check circuit breaker status                          │
│     ├─ Verify portfolio constraints                          │
│     ├─ ANY failure → REJECT (fail-closed)                    │
│     └─ Output: RiskCheck (passed/rejected + reasons)         │
│                          │                                   │
│  5. CONFIRM (HUMAN)      │                                   │
│     ├─ Present trade idea + risk assessment to user           │
│     ├─ Wait for explicit confirmation via Telegram            │
│     ├─ Timeout → auto-reject                                 │
│     └─ Output: confirmed / rejected                          │
│                          │                                   │
│  6. EXECUTE              │                                   │
│     ├─ Route to execution adapter (paper/backtest/dry/live)  │
│     ├─ Record execution in trade journal                     │
│     └─ Output: TradeExecution with fill details              │
│                          │                                   │
│  7. MONITOR              │                                   │
│     ├─ Track position against SL/TP                          │
│     ├─ Monitor for regime changes                            │
│     ├─ Check for early exit signals                          │
│     └─ Output: Position status updates                       │
│                          │                                   │
│  8. EXIT                 │                                   │
│     ├─ Trigger: SL hit / TP hit / manual / signal reversal   │
│     ├─ Close position in execution adapter                   │
│     ├─ Calculate realized PnL                                │
│     └─ Output: Closed trade with final metrics               │
│                          │                                   │
│  9. REVIEW               │                                   │
│     ├─ Compare outcome to hypothesis                         │
│     ├─ Score decision quality (was thesis correct?)           │
│     ├─ Flag patterns (e.g., "stopped out 3x on SOL today")   │
│     └─ Output: TradeReview with lessons                      │
│                          │                                   │
│  10. REMEMBER            │                                   │
│      ├─ Update performance statistics                        │
│      ├─ Adjust confidence calibration                        │
│      ├─ Write to persistent memory/journal                   │
│      └─ Output: Updated agent state                          │
│                          │                                   │
│  ────────── LOOP ──────── (return to step 1)                 │
└─────────────────────────────────────────────────────────────┘
```

## Loop Timing

| Mode | Cycle Time | Trigger |
|------|-----------|---------|
| **Manual scan** | On-demand | User sends `/scan` |
| **Scheduled scan** | Configurable (e.g., every 15m) | Cron/scheduler |
| **Backtest** | As fast as possible | Historical data replay |
| **Monitor** | Every 30s while position is open | Async background task |

## Critical Invariants

1. **Steps 4 and 5 are never skipped.** There is no code path from hypothesis to execution that bypasses risk validation and human confirmation.
2. **Step 4 is fail-closed.** Any check that errors (not just fails) results in rejection.
3. **Step 6 defaults to paper.** The execution adapter routes to paper trading unless two environment flags are explicitly set.
4. **Step 9 always runs.** Even rejected trades are reviewed and logged — the system learns from what it *didn't* do.
5. **Step 10 is append-only.** Memory is never deleted, only added to. The full decision history is preserved.

---

# 5. BEST PRACTICE MATRIX

## 5.1 Product

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Define what RUNECLAW is and is not, so every engineering decision has a reference frame |
| **Best practice** | One-sentence product definition that a judge can repeat back. "RUNECLAW is an explainable, simulation-first trading agent runtime." Not "an AI-powered next-gen trading platform leveraging cutting-edge..." |
| **Anti-patterns** | Describing features instead of the product. Calling everything "AI-powered." Positioning as a finished product instead of a hackathon prototype with a clear architecture. |
| **MVP version** | Telegram bot that scans, analyzes, risk-checks, and paper-trades with full audit logging |
| **Ambitious version** | Multi-exchange agent runtime with pluggable strategies, backtesting suite, and real-time dashboard |
| **Implementation notes** | The product definition is already solid. Do not over-expand scope. The MVP is the product. |
| **What judges care about** | Clarity of vision. Can you explain what this does in 30 seconds? |
| **What users care about** | Does it work? Can I trust it? Can I understand what it did? |

## 5.2 Architecture

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Modular system design that separates concerns and enables independent testing |
| **Best practice** | Each module has one job. Perception does not know about execution. Risk engine does not know about UI. Data flows in one direction through the pipeline. |
| **Anti-patterns** | God classes. Mixing API calls with business logic. Risk checks embedded in execution code. Circular dependencies between modules. |
| **MVP version** | Python modules with clean imports: `core/`, `risk/`, `skills/`, `utils/`. Current structure is already correct. |
| **Ambitious version** | Microservice-ready separation with gRPC or message queue between layers |
| **Implementation notes** | The current architecture is well-separated. Main gap: the Telegram handler does some orchestration that should be in the engine. Refactoring priority: medium. |
| **What judges care about** | Can they trace a request from input to output through the code? Is the architecture visible in the repo structure? |
| **What users care about** | Reliability. Predictable behavior. No mysterious failures. |

## 5.3 Perception

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Convert raw market data into structured signals the decision engine can consume |
| **Best practice** | Perception is read-only. It never modifies state. It produces typed data structures (not raw JSON). Multiple perception modules can run in parallel. |
| **Anti-patterns** | Fetching data inside the decision engine. Hardcoded API responses. Perception modules that also make trading decisions. |
| **MVP version** | Market scanner (top movers, volume anomalies) + OHLCV candle fetch + RSI/MACD/BB computation. All implemented. |
| **Ambitious version** | Orderbook depth analysis, funding rate signals, cross-exchange correlation, social sentiment scoring, on-chain whale tracking |
| **Implementation notes** | Current scanner and analyzer cover MVP well. For hackathon impact, add one "impressive" perception module — **regime detection** (trend/range/chop classification) would demonstrate the most sophistication for the least code. |
| **What judges care about** | Data diversity. Are you using more than just price? Do you understand market microstructure? |
| **What users care about** | Signal quality. Fewer, better signals beat more noise. |

## 5.4 Decisioning

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Transform signals into actionable trade hypotheses with structured reasoning |
| **Best practice** | Every trade idea has: direction, entry, SL, TP, confidence score, R:R ratio, and natural language reasoning. The reasoning is not decoration — it is the primary output. |
| **Anti-patterns** | Binary buy/sell signals with no context. Confidence scores with no calibration. "The AI thinks BTC will go up" with no supporting structure. |
| **MVP version** | LLM thesis generation with technical indicator context. Falls back to rule-based analysis when no LLM key is set. Current implementation handles this. |
| **Ambitious version** | Ensemble scoring (multiple LLM models + rule-based + statistical), confidence calibration from historical accuracy, hypothesis versioning |
| **Implementation notes** | The LLM fallback is a strength, not a weakness. It demonstrates that the system works without external API dependencies. Emphasize this in the demo. |
| **What judges care about** | Explainability. Can they read the reasoning and evaluate whether it makes sense? |
| **What users care about** | Accuracy of thesis. Does the reasoning reflect what is actually happening in the market? |

## 5.5 Risk

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Independent gate that can veto any trade, regardless of signal confidence |
| **Best practice** | Fail-closed. Every check must pass. Error = rejection (not bypass). Circuit breaker is automatic and non-overridable. Risk engine is a separate module that the orchestrator calls — it is not embedded in the execution path. |
| **Anti-patterns** | Risk checks that log warnings but don't block. "Soft" limits that can be overridden by confidence. Risk engine that only runs in production mode. Disabling risk in backtesting. |
| **MVP version** | 18 pre-trade checks + circuit breaker + portfolio constraints. All implemented and fail-closed. |
| **Ambitious version** | Correlation risk (multiple positions in same sector), Greeks-aware risk for derivatives, VaR calculation, Monte Carlo stress testing |
| **Implementation notes** | The current risk engine is the strongest differentiator. In the demo, show a trade being rejected by the risk engine. This is more impressive than showing a trade being executed. |
| **What judges care about** | Safety. Does this team understand that autonomous trading is dangerous? Do they have engineering controls, not just good intentions? |
| **What users care about** | Capital preservation. The risk engine protects them from the system and from themselves. |

## 5.6 Execution

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Route trade intents to the appropriate execution environment |
| **Best practice** | Execution adapter pattern. Same interface for paper, backtest, dry-run, and live. The decision engine does not know which mode is active. Mode is configured at startup, not per-trade. |
| **Anti-patterns** | `if mode == 'live': exchange.place_order()` scattered throughout the codebase. Different code paths for simulation vs live. Testing only in paper mode and assuming live will work the same way. |
| **MVP version** | Paper trading with local portfolio tracking. Current implementation is solid. |
| **Ambitious version** | Bitget API integration with order management, partial fills, slippage modeling |
| **Implementation notes** | For hackathon: paper trading IS the product. Live trading is a configuration option that exists but is intentionally disabled. Show the config that would enable it — but don't enable it. |
| **What judges care about** | Architecture maturity. Is the execution layer properly abstracted? Could this actually be connected to a real exchange? |
| **What users care about** | Reliability. Paper trades that accurately reflect what would have happened live. |

## 5.7 Explainability

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Every decision the system makes can be understood and audited after the fact |
| **Best practice** | Structured decision records: what was the signal, what was the thesis, what did risk say, what did the human say, what was executed, what was the outcome. Every record includes timestamps, input data, and reasoning. |
| **Anti-patterns** | `log.info("trade executed")` with no context. Explanations generated after the fact instead of at decision time. Explanations that don't match what actually happened. |
| **MVP version** | JSON structured logging with three channels (trade, risk, system). Every TradeIdea includes reasoning field. All implemented. |
| **Ambitious version** | Decision replay UI, counterfactual analysis ("what if risk limit was 3% instead of 2%?"), visual decision tree |
| **Implementation notes** | In the demo, show the JSON log of a trade. Walk through the reasoning. This is the "ah-ha" moment for judges — most trading bots cannot do this. |
| **What judges care about** | Can they audit a decision after the fact? Is the system transparent? |
| **What users care about** | Learning. Understanding why a trade was taken helps them improve. |

## 5.8 Frontend UX

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Visual layer for monitoring, command, and review |
| **Best practice** | The frontend reflects the system state, not the other way around. Trading decisions happen in the agent loop, not in the UI. The UI is a window into the system, not the system itself. |
| **Anti-patterns** | Building the frontend first. Making the UI the primary interaction method before the agent loop works. Spending more time on CSS than on the agent pipeline. |
| **MVP version** | Premium landing page (completed) + Telegram as primary command interface (implemented) |
| **Ambitious version** | Real-time dashboard with position monitoring, trade timeline, risk gauge, decision log viewer |
| **Implementation notes** | The landing page is done and polished. Telegram is the primary interface. If time permits, a simple dashboard showing portfolio state and recent decisions would be high-impact. But it is NOT mandatory. |
| **What judges care about** | Polish and professionalism. A clean landing page signals a team that cares about presentation. But substance beats style. |
| **What users care about** | Usability. Can they do what they need to do without reading documentation? Telegram commands handle this. |

## 5.9 GitHub Repo Design

| Dimension | Detail |
|-----------|--------|
| **Purpose** | The repo IS the submission. Judges will read the README, scan the file structure, and may browse 2-3 files. |
| **Best practice** | README is the pitch. File structure mirrors the architecture. Each directory has a clear purpose. No junk files. `.env.example` is present. Setup instructions work on first try. |
| **Anti-patterns** | README that is a feature list with no architecture explanation. Flat file structure with 30 files in root. No `.gitignore`. Committed `.env` files. Dead code. |
| **MVP version** | Clean repo with README, architecture diagram, setup instructions, `.env.example`, organized `bot/` directory. Current state is good. |
| **Ambitious version** | CI/CD with linting, Docker support, contributor guidelines, issue templates, project board |
| **Implementation notes** | Current structure is correct. GitHub Actions CI, Dockerfile, Makefile are all in place. Main improvements: ensure README architecture diagram matches actual code, add a "Quick Demo" section with exact commands to run. |
| **What judges care about** | Can they understand the project in 60 seconds from the README? Is the code organized? |
| **What users care about** | Can they clone and run it? Does `make install && make run-cli` work? |

## 5.10 GitBook Documentation

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Comprehensive reference documentation for users, developers, and evaluators |
| **Best practice** | Clear hierarchy: Getting Started → Architecture → Commands → Risk → API Reference. Each page answers one question. Code examples are real, not pseudo-code. |
| **Anti-patterns** | Documentation that is the README copied into GitBook. Pages with no content ("Coming soon!"). Documentation that contradicts the code. |
| **MVP version** | 8 pages covering all major topics. Current state is comprehensive. |
| **Ambitious version** | Interactive API explorer, embedded code examples that pull from the repo, video walkthroughs |
| **Implementation notes** | Current GitBook content is solid. Priority: ensure the risk framework page is detailed and accurate — this is the page judges will read most carefully. |
| **What judges care about** | Does the documentation demonstrate depth of thinking? Is the risk framework well-articulated? |
| **What users care about** | Can they set it up? Can they understand the commands? Can they customize it? |

## 5.11 Demo Flow

| Dimension | Detail |
|-----------|--------|
| **Purpose** | 3-minute live demonstration that proves the system works and communicates the value proposition |
| **Best practice** | Open with the problem (15s). Show the solution working (2m). Close with architecture + safety (45s). Every demo step is pre-tested. No live API calls that could fail. |
| **Anti-patterns** | Starting with the architecture slide. Showing code before showing the product. Demo that requires market conditions to cooperate. Demo that starts with "let me just..." |
| **MVP version** | Telegram bot demo: `/scan` → `/analyze BTC` → show trade idea → show risk check → confirm → show portfolio. Pre-recorded backup video. |
| **Ambitious version** | Live dashboard + Telegram side-by-side, showing real-time updates as commands are sent. Backtest replay showing strategy performance. |
| **Implementation notes** | Record a backup demo video before the presentation. Test every command in the exact order you'll demo them. Have a fallback for every step. The demo script in `scripts/demo_script.md` covers this. |
| **What judges care about** | Does it work? Is the team prepared? Do they understand their own system? |
| **What users care about** | Not applicable — users aren't at the demo. But: would a user watching this video want to try it? |

## 5.12 Submission Packaging

| Dimension | Detail |
|-----------|--------|
| **Purpose** | Everything a judge needs to evaluate the project, organized for minimal friction |
| **Best practice** | Single repo with clear structure. README is the entry point. Demo video link at the top. Working setup instructions. Sample outputs in the repo. |
| **Anti-patterns** | Multiple repos. README that assumes context. Setup instructions that don't work. No demo video. Submitting a Google Doc instead of a repo. |
| **MVP version** | GitHub repo + GitBook + demo video link + Telegram bot link |
| **Ambitious version** | Deployed dashboard + live Telegram bot + published demo video + pitch deck |
| **Implementation notes** | The repo structure is complete. Priority: record demo video, ensure all links in README are live, test setup instructions on a clean machine. |
| **What judges care about** | How easy is it to evaluate? Can they understand the project without running it? |
| **What users care about** | Not applicable at submission stage. |

---

# 6. DELIVERY ROADMAP

## Phase Overview

```
PHASE 1: CORE COMPLETE          ██████████████████ DONE
PHASE 2: INTEGRATION POLISH     ████████░░░░░░░░░░ IN PROGRESS
PHASE 3: DEMO PREPARATION       ░░░░░░░░░░░░░░░░░░ NEXT
PHASE 4: SUBMISSION PACKAGING   ░░░░░░░░░░░░░░░░░░ FINAL
```

## Phase 1: Core Complete (DONE)

Everything below is built and in the repository:

| Component | Status | File |
|-----------|--------|------|
| Market scanner | Done | `bot/core/market_scanner.py` |
| AI analyzer | Done | `bot/core/analyzer.py` |
| Risk engine (fail-closed) | Done | `bot/risk/risk_engine.py` |
| Portfolio tracker | Done | `bot/risk/portfolio.py` |
| Skill registry | Done | `bot/skills/skill_registry.py` |
| Telegram handler | Done | `bot/skills/telegram_handler.py` |
| Audit logger | Done | `bot/utils/logger.py` |
| Data models | Done | `bot/utils/models.py` |
| Configuration | Done | `bot/config.py` |
| Main entry point | Done | `bot/main.py` |
| System prompt | Done | `bot/prompts/system_prompt.md` |
| Skill definitions | Done | `bot/prompts/skill_definitions.yaml` |
| Landing page | Done | `website/index.html` |
| README | Done | `README.md` |
| GitBook (8 pages) | Done | `docs/gitbook/` |
| Docker setup | Done | `Dockerfile`, `docker-compose.yml` |
| CI/CD | Done | `.github/workflows/lint.yml` |
| Dev tooling | Done | `Makefile`, `.gitignore`, `.env.example` |
| Demo materials | Done | `scripts/demo_script.md`, `demo/` |
| License | Done | `LICENSE` |

## Phase 2: Integration Polish (CURRENT PRIORITY)

| Task | Priority | Effort | Impact | Notes |
|------|----------|--------|--------|-------|
| **Test full Telegram flow end-to-end** | P0 | Medium | Critical | Ensure `/scan` → `/analyze` → `/trade` → `/portfolio` works as a complete flow |
| **Add backtest command** | P1 | Medium | High | `/backtest BTC 7d` — replay last 7 days through the pipeline, show results |
| **Add regime detection** | P1 | Low | High | Classify market as trend/range/chop based on ADX + BB width. High judge-appeal for low effort |
| **Ensure paper portfolio persists** | P1 | Low | Medium | Save portfolio state to JSON file so it survives restarts |
| **Add `/explain` command** | P2 | Low | Medium | Show full decision log for a specific trade |
| **Add API endpoint layer** | P3 | Medium | Low | FastAPI wrapper around existing skills — defer unless time permits |

## Phase 3: Demo Preparation

| Task | Priority | Effort | Notes |
|------|----------|--------|-------|
| **Write exact demo command sequence** | P0 | Low | Pre-test every command in order |
| **Record backup demo video** | P0 | Low | Screen recording of full demo flow |
| **Prepare sample data for demo** | P0 | Low | Pre-populate portfolio with interesting positions |
| **Test on clean machine** | P0 | Medium | Clone repo, follow setup instructions, verify it works |
| **Prepare 1-slide architecture diagram** | P1 | Low | For the pitch — not a slide deck, just one reference image |
| **Prepare risk engine demo** | P1 | Low | Show a trade being REJECTED by risk. This is the money shot. |

## Phase 4: Submission Packaging

| Task | Priority | Notes |
|------|----------|-------|
| **Verify all README links** | P0 | GitHub, GitBook, Telegram — all must be live |
| **Push final code to GitHub** | P0 | Clean commit history, no debug code |
| **Publish GitBook** | P0 | Ensure all pages render correctly |
| **Upload demo video** | P1 | Link in README |
| **Final README review** | P0 | Read from a judge's perspective — does it make sense in 60 seconds? |
| **Submit to hackathon** | P0 | Follow exact submission instructions |

## What to Build First (Priority Order)

1. End-to-end Telegram flow test (validates everything works together)
2. Regime detection module (high impact, low effort, differentiator)
3. Demo preparation and recording
4. Backtest command
5. Submission packaging

## What to Defer

- Real-time WebSocket dashboard (high effort, not required)
- Multi-exchange support (scope creep)
- Advanced backtesting with walk-forward optimization (too complex for hackathon)
- Live trading integration testing (not needed — simulation-first is the point)
- API key authentication system (no external users yet)
- Mobile app (way out of scope)

## What is Mandatory for Submission

| Item | Why |
|------|-----|
| Working Telegram bot | Primary demo interface |
| Complete risk engine | Core differentiator |
| Paper trading flow | Proves the system works |
| GitHub repo with README | Submission artifact |
| GitBook documentation | Shows depth of thinking |
| Demo video or live demo | Proves it's real |

## What is Optional Polish

| Item | Impact if Present |
|------|-------------------|
| Landing page | Already done — significant credibility boost |
| Backtest command | Shows time-series thinking |
| Dashboard | Visual wow-factor |
| CI/CD pipeline | Shows engineering maturity |
| Docker setup | Shows deployment thinking |

---

# 7. IMPLEMENTATION REFERENCE

## Repository Structure (Current)

```
runeclaw/
├── bot/
│   ├── core/
│   │   ├── engine.py          # Agent orchestrator
│   │   ├── market_scanner.py  # Perception: market signals
│   │   └── analyzer.py        # Decision: thesis generation
│   ├── risk/
│   │   ├── risk_engine.py     # Risk gate (fail-closed)
│   │   └── portfolio.py       # Paper trading ledger
│   ├── skills/
│   │   ├── skill_registry.py  # Modular skill system
│   │   └── telegram_handler.py # Telegram command interface
│   ├── utils/
│   │   ├── models.py          # Pydantic data models
│   │   └── logger.py          # Structured audit logging
│   ├── prompts/
│   │   ├── system_prompt.md   # Agent identity and rules
│   │   └── skill_definitions.yaml
│   ├── config.py              # Environment configuration
│   ├── main.py                # Entry point
│   └── requirements.txt
├── website/
│   └── index.html             # Premium landing page
├── docs/
│   ├── gitbook/               # 8-page GitBook documentation
│   └── MASTER_BUILD_PLAN.md   # This document
├── demo/
│   ├── sample_output.json     # Example trade idea
│   ├── sample_risk_check.json # Example risk evaluation
│   └── sample_portfolio.json  # Example portfolio state
├── scripts/
│   └── demo_script.md         # 3-minute demo script
├── .github/workflows/lint.yml # CI pipeline
├── .env.example
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── Makefile
├── LICENSE
└── README.md
```

## Key Design Decisions and Rationale

| Decision | Rationale |
|----------|-----------|
| **Python, not TypeScript** | Quant/trading ecosystem is Python. Libraries (pandas, numpy, ta-lib) are Python-native. Judges in the trading track expect Python. |
| **Telegram, not web dashboard** | Telegram is lower friction, mobile-native, and aligns with the Bitget Agent Hub agent-as-chat paradigm. Dashboard is secondary. |
| **Pydantic models, not dicts** | Type safety, validation, serialization. Every data structure is documented by its schema. Judges can read the models to understand the system. |
| **Fail-closed risk engine** | The single most important architectural decision. Demonstrates safety thinking that most hackathon projects lack entirely. |
| **Paper trading as default** | Not a compromise — a feature. The system proves its value without risking capital. |
| **JSON audit logging** | Machine-readable, human-readable, and diff-able. No database required for MVP. |
| **Skill-based architecture** | Aligns with the agent-as-tool paradigm. Each skill is independently testable and documentable. |
| **LLM fallback to rules** | System works without API keys. This is critical for judges who want to test it. |

## Tradeoff Acknowledgments

| Tradeoff | Chosen | Alternative | Why |
|----------|--------|-------------|-----|
| Backtest fidelity | Simplified (fill at close) | Full order book simulation | Sufficient for demonstrating the concept. Full simulation is weeks of work. |
| Data source | Bitget REST API (polled) | WebSocket streaming | Polling is simpler, reliable, and sufficient for scan-on-demand. Streaming adds complexity. |
| Portfolio persistence | JSON file | SQLite / PostgreSQL | No database dependency. JSON is readable, versionable, and sufficient for single-user MVP. |
| Multi-asset | Sequential scanning | Parallel async | Sequential is simpler to debug and demo. Parallelism is a scaling concern, not a correctness concern. |
| LLM provider | OpenAI-compatible API | Multi-provider | One integration, clean fallback. Multi-provider adds config complexity with no demo benefit. |

## What is Mocked vs Real vs Production-Capable

| Component | Status | Notes |
|-----------|--------|-------|
| Market data fetch | **Real** | Hits Bitget public API (no auth required) |
| Technical indicators | **Real** | Computed from real OHLCV data |
| LLM thesis generation | **Real** (when API key set) | Falls back to rule-based if no key |
| Risk engine checks | **Real** | All 20 checks execute against actual portfolio state |
| Paper trading | **Real** (simulated fills) | Fills at current market price, tracks PnL accurately |
| Portfolio tracking | **Real** | Full position lifecycle, PnL, drawdown |
| Audit logging | **Real** | Every decision written to structured JSON |
| Telegram commands | **Real** | Full bot interface with inline keyboards |
| Live order execution | **Disabled** | Code path exists but is locked behind double-flag config |
| Orderbook data | **Mocked** | Would use WebSocket in production |
| Funding rate signals | **Mocked** | Would use Bitget funding API in production |
| Social sentiment | **Mocked** | Would use news/social API in production |
| Backtesting engine | **Partially built** | Pipeline exists, needs historical data replay wrapper |
| Dashboard | **Landing page only** | Real-time monitoring dashboard is not built |

---

*Document version: 1.0*
*Project: RUNECLAW — Humanoid Traders*
*Target: Bitget AI Builder Base Camp Hackathon*
*Architecture status: Core complete. Integration polish phase.*
