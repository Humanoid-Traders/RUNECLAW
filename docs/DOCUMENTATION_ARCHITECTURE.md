# RUNECLAW — Documentation Architecture
## Technical Documentation Blueprint for Hackathon Judges, Developers, and Reviewers

---

# TABLE OF CONTENTS

1. [README Outline](#1-readme-outline)
2. [GitBook Sitemap](#2-gitbook-sitemap)
3. [Per-Page Breakdown](#3-per-page-breakdown)
4. [Documentation Best-Practice Notes](#4-documentation-best-practice-notes)

---

# 1. README OUTLINE

## Purpose
The README is the first 30 seconds a judge spends on RUNECLAW. It must answer three questions instantly: *What is this? Why should I care? How do I run it?*

## Audience
Hackathon judges, GitHub visitors, developers evaluating the repo.

## Hero Copy

```
RUNECLAW — AI Trading Command Core
Forged in Volatility. Governed by Discipline.

An autonomous, simulation-first crypto trading agent that perceives markets,
generates explainable trade theses, enforces fail-closed risk controls,
and logs every decision for full auditability.

Built for the Bitget GetClaw Hackathon by Humanoid Traders.
```

## Section Outline

### 1. Hero Block
- ASCII art / logo
- One-line tagline: `AI Trading Command Core | Forged in Volatility. Governed by Discipline.`
- Badges: Python 3.11+, MIT, Paper Trading, Bitget, GetClaw 2025
- Quick links: GitHub, GitBook, Telegram

### 2. Why This Matters (Judge-Focused)
**Title:** `Why This Matters`

> Most hackathon trading bots are GPT wrappers that call exchange APIs and hope for the best. RUNECLAW is different.
>
> - **Simulation-first.** Live trading is disabled by default. Every trade is paper until explicitly unlocked with two environment flags.
> - **Fail-closed risk.** Seven independent pre-trade checks. One failure = rejection. No overrides, no exceptions.
> - **Human-in-the-loop.** No trade executes without explicit confirmation via Telegram inline keyboard.
> - **Explainable.** Every trade idea includes the indicators used, the LLM reasoning, and the confidence score.
> - **Auditable.** Every decision — scan, analysis, risk check, confirmation, execution — is logged as structured JSON.
>
> This is not a bot that trades. This is an agent runtime that proposes, validates, and — only with permission — executes.

### 3. Architecture Diagram
ASCII pipeline: `Market Scanner → AI Analyzer → Risk Gate → Human Confirm → Execute (Paper) → Audit Log`
- Full box-and-arrow diagram showing Telegram Bot, Skill Registry, Engine, Scanner, Analyzer, Risk Engine, Portfolio Tracker
- Label the fail-closed gate explicitly

### 4. Core Pipeline
Numbered list with one sentence per stage:
1. **Perceive** — Scanner fetches all Bitget USDT pairs, ranks by 24h change, detects volume spikes (2x rolling avg)
2. **Analyze** — Technical indicators (RSI-14, MACD 12/26/9, Bollinger 20/2, ATR) + LLM directional thesis
3. **Risk Gate** — 18 risk checks: circuit breaker, position size (20% notional cap, 2% risk budget), daily loss (5%), drawdown (10%), max positions (5), R:R (>1.2), confidence (>0.60), correlation, loss streak, entry sanity, stop-loss required, stale data, cooldown, portfolio exposure, symbol exposure, volatility guard
4. **Confirm** — Human reviews trade idea via Telegram, taps Confirm or Reject
5. **Re-Check** — Risk is re-evaluated at confirmation time (market may have moved)
6. **Execute** — Paper trade opens, SL/TP are set, portfolio updates
7. **Audit** — Every step logged as structured JSONL across three channels

### 5. Quickstart

```bash
# Clone
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW

# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r bot/requirements.txt
cp .env.example .env

# Run (no API keys needed for paper mode)
python -m bot.main --mode cli

# Verify
# Type: get_portfolio → see $10,000 paper balance
# Type: check_risk → see all-clear risk status
# Type: quit → exit
```

### 6. Telegram Commands Table
| Command | Description | Example |
|---------|-------------|---------|
| `/scan` | Scan market for top movers and volume spikes | Returns top 5 signals |
| `/analyze BTC` | Run AI analysis on a specific asset | Returns TradeIdea with entry/SL/TP |
| `/portfolio` | View paper portfolio summary | Balance, equity, PnL, win rate |
| `/trade` | View and confirm/reject pending trades | Inline keyboard: Confirm / Reject |
| `/risk` | Risk metrics and circuit breaker status | Drawdown, daily loss, CB state |
| `/status` | Bot mode, equity, open positions | System health overview |
| `/help` | List all available commands | Full command reference |

### 7. Project Structure
Tree view with one-line descriptions for every file.

### 8. Safety & Risk
Bullet list of the 16 safety guarantees. Include the disclaimer about hackathon use.

### 9. Tech Stack Table
Python 3.11+, Bitget via ccxt, OpenAI GPT-4o, NumPy, Pydantic v2, python-telegram-bot 20.x, Structured JSON logging, python-dotenv.

### 10. Links
GitHub, GitBook, Telegram group, License (MIT).

## Key Diagrams
- ASCII architecture diagram (box-and-arrow, fits terminal width)
- Pipeline flow: SCAN → ANALYZE → RISK → CONFIRM → EXECUTE → AUDIT

## Common Mistakes to Avoid
- Do NOT put the entire architecture doc in the README. Link to it.
- Do NOT use marketing language. Judges are engineers.
- Do NOT omit the disclaimer. Paper trading + hackathon context must be stated.
- Do NOT hide the quickstart below the fold. It should be reachable in <3 scroll-lengths.

---

# 2. GITBOOK SITEMAP

## Sidebar Structure

```
RUNECLAW
├── Overview                          # docs/overview.md
├── Getting Started                   # docs/setup.md
├── Architecture                      # docs/architecture.md
├── Modules
│   ├── Perception (Market Scanner)   # docs/modules/perception.md
│   ├── Decision Engine (Analyzer)    # docs/modules/decision-engine.md
│   ├── Risk Engine                   # docs/modules/risk-engine.md
│   ├── Execution                     # docs/modules/execution.md
│   └── Memory & Journal              # docs/modules/memory-and-journal.md
├── Paper Trading                     # docs/paper-trading.md
├── Backtesting                       # docs/backtesting.md
├── API Reference                     # docs/api.md
├── Demo Guide                        # docs/demo-guide.md
├── FAQ                               # docs/faq.md
└── Submission                        # docs/submission.md
```

## GitBook SUMMARY.md

```markdown
# Summary

* [Overview](overview.md)
* [Getting Started](setup.md)
* [Architecture](architecture.md)
* [Modules](modules/README.md)
  * [Perception](modules/perception.md)
  * [Decision Engine](modules/decision-engine.md)
  * [Risk Engine](modules/risk-engine.md)
  * [Execution](modules/execution.md)
  * [Memory & Journal](modules/memory-and-journal.md)
* [Paper Trading](paper-trading.md)
* [Backtesting](backtesting.md)
* [API Reference](api.md)
* [Demo Guide](demo-guide.md)
* [FAQ](faq.md)
* [Submission](submission.md)
```

---

# 3. PER-PAGE BREAKDOWN

---

## 3.1 — README.md

**Purpose:** First impression. Convince a judge in 30 seconds that this is a serious, well-architected project — not a GPT wrapper.

**Audience:** Hackathon judges, GitHub visitors, potential contributors.

**Section Outline:**
1. Hero block (logo, tagline, badges, links)
2. Why This Matters (judge-focused value proposition)
3. Architecture diagram (ASCII)
4. Core pipeline (7-step numbered list)
5. Quickstart (clone → run in <60 seconds)
6. Telegram commands table
7. Project structure tree
8. Safety & risk guarantees
9. Tech stack table
10. Links and license

**Key Diagrams:**
- ASCII architecture (box-and-arrow)
- Pipeline flow (inline, horizontal)

**Screenshots / Evidence:**
- None in README (keep it text-only for GitHub rendering)
- Link to demo guide for screenshots

**Common Mistakes:**
- Putting too much content — README should be <300 lines
- Using shield badges for vanity metrics instead of meaningful status
- Omitting the safety disclaimer
- Making quickstart require API keys (CLI mode should work without any)

---

## 3.2 — docs/overview.md

**Purpose:** Expanded product overview for readers who want more than the README. This is the "what and why" page.

**Audience:** Judges doing a deep review, developers considering contributing.

**Section Outline:**
1. **What is RUNECLAW** — 2-paragraph elevator pitch
2. **Design Philosophy** — Three pillars:
   - Simulation-first (default safe, opt-in risk)
   - Fail-closed risk (reject by default, approve by exception)
   - Explainable decisions (every output has reasoning)
3. **How It Works** — High-level pipeline (Perceive → Score → Hypothesize → Validate → Confirm → Execute → Monitor → Audit)
4. **What Makes This AI-Native** — Comparison table: Traditional Dashboard vs RUNECLAW agent
5. **Target Users** — Table: Quantitative retail traders, developer-traders, risk-conscious algo traders, hackathon judges
6. **Bitget Alignment** — How RUNECLAW maps to the GetClaw hackathon criteria and Bitget Agent Hub
7. **Current Status** — What works now (paper trading, CLI, Telegram) vs roadmap (backtesting, live adapter, multi-exchange)

**Key Diagrams:**
- Agent operating loop (circular: Perceive → Score → Hypothesize → Validate → Confirm → Execute → Monitor → Audit → Learn)
- Comparison table (dashboard vs agent)

**Screenshots / Evidence:**
- Link to demo guide for live output examples

**Common Mistakes:**
- Writing marketing copy instead of technical overview
- Not stating current limitations honestly
- Conflating "simulation-first" with "doesn't work" — frame it as a design choice

---

## 3.3 — docs/architecture.md

**Purpose:** Full system design reference. A reviewer should be able to understand every module boundary, data flow, and failure mode from this page alone.

**Audience:** Technical judges, architects, reviewers evaluating code quality.

**Section Outline:**
1. **System Diagram** — Full architecture with all components, data flows, and failure paths
2. **Component Inventory**
   - Table: Component → File → Responsibility → Inputs → Outputs
   - `MarketScanner` → `bot/core/market_scanner.py` → Fetch tickers, rank movers, detect volume spikes → Bitget API → `list[MarketSignal]`
   - `Analyzer` → `bot/core/analyzer.py` → Compute indicators, generate LLM thesis → `MarketSignal` + OHLCV → `TradeIdea`
   - `RiskEngine` → `bot/risk/risk_engine.py` → 18 risk checks, circuit breaker → `TradeIdea` + `PortfolioState` → `RiskCheck`
   - `PortfolioTracker` → `bot/risk/portfolio.py` → Paper ledger, PnL, drawdown → `TradeIdea` → `TradeExecution`
   - `RuneClawEngine` → `bot/core/engine.py` → Central orchestrator → All above → Pipeline execution
   - `SkillRegistry` → `bot/skills/skill_registry.py` → Modular capability system → Engine → Skill output
   - `TelegramHandler` → `bot/skills/telegram_handler.py` → User interface, inline keyboards → Telegram API → Commands
   - `AuditLogger` → `bot/utils/logger.py` → Structured JSON logging → All components → JSONL files
3. **Data Flow** — Step-by-step trace of a single trade from scan to execution:
   ```
   Bitget API → MarketScanner.scan()
     → list[MarketSignal] → Engine._analyze_signal()
       → Analyzer.analyze() → TradeIdea
         → RiskEngine.evaluate() → RiskCheck
           → [APPROVED] → pending_ideas dict
             → Telegram inline keyboard → Human confirms
               → Engine.confirm_trade()
                 → RiskEngine.evaluate() (re-check)
                   → [APPROVED] → PortfolioTracker.open_position()
                     → TradeExecution → audit log
   ```
4. **Data Models** — Table of all Pydantic schemas with fields:
   - `MarketSignal` (7 fields)
   - `TradeIdea` (10 fields + computed `risk_reward_ratio`)
   - `RiskCheck` (10 fields)
   - `TradeExecution` (12 fields)
   - `PortfolioState` (9 fields)
5. **Failure Modes** — How each component fails:
   - Scanner: exchange error → return empty list → no trades
   - Analyzer: LLM error → rule-based fallback → still produces idea
   - Analyzer: <30 candles → return None → skip
   - Risk: any check fails → REJECTED → no trade
   - Risk: daily loss or drawdown breach → circuit breaker trips → all future trades blocked
   - Engine: unhandled exception → caught in `_tick()` → logged, loop continues
   - Confirmation: trade not found → return error message
   - Live trading: `CONFIG.is_live()` returns True only when `LIVE_TRADING_ENABLED=true` AND `SIMULATION_MODE=false`
6. **Configuration Hierarchy** — Dataclass tree: `AppConfig` → `RiskLimits`, `ExchangeConfig`, `TelegramConfig`, `LLMConfig`
7. **Logging Architecture** — Three channels (`trade.jsonl`, `risk.jsonl`, `system.jsonl`), JSON schema per entry, `audit()` function signature

**Key Diagrams:**
- Full architecture diagram (ASCII or Mermaid, reproducible)
- Data flow trace (numbered steps)
- Failure mode decision tree
- Config hierarchy tree

**Screenshots / Evidence:**
- Sample log entry from `demo/sample_output.json`
- Sample risk check from `demo/sample_risk_check.json`

**Common Mistakes:**
- Showing architecture without failure modes — judges look for this
- Not mapping files to components (makes code review harder)
- Drawing diagrams that don't match the actual code
- Omitting the re-check on confirmation (this is a differentiator)

---

## 3.4 — docs/setup.md

**Purpose:** Get a developer from zero to running in under 5 minutes. No ambiguity.

**Audience:** Developers, hackathon judges testing the repo.

**Section Outline:**
1. **Prerequisites**
   - Python 3.11+ (required)
   - Bitget account (optional — paper mode works without it)
   - Telegram bot token from @BotFather (optional — CLI mode works without it)
   - OpenAI API key (optional — rule-based fallback if absent)
2. **Installation**
   ```bash
   git clone https://github.com/Humanoid-Traders/RUNECLAW.git
   cd RUNECLAW
   python -m venv .venv
   source .venv/bin/activate   # Linux/macOS
   .venv\Scripts\activate      # Windows
   pip install -r bot/requirements.txt
   ```
3. **Configuration**
   - `cp .env.example .env`
   - Environment variable table with Required/Optional column:
     | Variable | Required | Default | Description |
     |----------|----------|---------|-------------|
     | `SIMULATION_MODE` | — | `True` | Paper trading on/off |
     | `LIVE_TRADING_ENABLED` | — | `False` | Live execution gate |
     | `PAPER_BALANCE_USD` | — | `10000` | Starting paper balance |
     | `TELEGRAM_BOT_TOKEN` | For Telegram mode | — | Bot token from BotFather |
     | `TELEGRAM_CHAT_ID` | — | — | Restrict to specific chat |
     | `BITGET_API_KEY` | For live data | — | Bitget API key |
     | `BITGET_API_SECRET` | For live data | — | Bitget API secret |
     | `BITGET_PASSPHRASE` | For live data | — | Bitget passphrase |
     | `LLM_API_KEY` | — | — | OpenAI-compatible API key |
     | `LLM_MODEL` | — | `gpt-4o` | Model name |
     | `MAX_POSITION_PCT` | — | `2.0` | Max position size (% of equity) |
     | `MAX_DAILY_LOSS_PCT` | — | `5.0` | Daily loss limit (%) |
     | `MAX_DRAWDOWN_PCT` | — | `10.0` | Max drawdown limit (%) |
     | `MAX_OPEN_POSITIONS` | — | `5` | Concurrent position limit |
     | `SCAN_INTERVAL` | — | `60` | Seconds between scans |
4. **Running**
   - **CLI Mode** (no dependencies, immediate):
     ```bash
     python -m bot.main --mode cli
     ```
     Expected: `runeclaw>` prompt. Type `get_portfolio`, see $10,000 balance.
   - **Telegram Mode**:
     ```bash
     python -m bot.main --mode telegram
     ```
     Expected: bot polls for updates, send `/help` in Telegram.
   - **Scan Mode** (one-shot):
     ```bash
     python -m bot.main --mode scan
     ```
5. **Verification Checklist**
   - [ ] `get_portfolio` returns $10,000 balance
   - [ ] `check_risk` returns all-clear, circuit breaker OK
   - [ ] `scan_market` returns signals (requires Bitget API key) or graceful error
   - [ ] Logs appear in `logs/` directory as JSONL
6. **Docker** (alternative)
   ```bash
   docker compose up --build
   ```
7. **Troubleshooting**
   - "No module named bot" → ensure you're in the project root
   - "ccxt import error" → `pip install ccxt`
   - "No signals detected" → normal without API keys, scanner returns empty list gracefully

**Key Diagrams:** None needed — this is procedural.

**Screenshots / Evidence:**
- Terminal screenshot of successful CLI startup showing banner + `runeclaw>` prompt
- Terminal screenshot of `get_portfolio` output

**Common Mistakes:**
- Requiring API keys for basic functionality — CLI paper mode must work with zero config
- Not showing expected output for each command
- Missing Windows instructions (`source` vs `Scripts\activate`)
- Not mentioning Docker as an alternative

---

## 3.5 — docs/modules/perception.md

**Purpose:** Deep dive into the market perception layer — what the system sees and how it decides what to look at.

**Audience:** Technical reviewers, developers extending the scanner.

**Section Outline:**
1. **Role in the Pipeline** — Perception is stage 1. It answers: "What is happening in the market right now?"
2. **Implementation** — `bot/core/market_scanner.py`, class `MarketScanner`
3. **Data Source** — Bitget spot market via ccxt async. Fetches all tickers, filters for `/USDT` pairs.
4. **Filtering Logic**
   - Minimum volume threshold: $50,000 24h quote volume
   - Price > 0 (sanity check)
5. **Volume Spike Detection**
   - Rolling window of last 20 volume observations per symbol
   - Spike = current volume > 2x rolling average
   - Requires minimum 5 observations before detection activates
   - Implementation: `_detect_volume_spike(symbol, current_vol) -> bool`
6. **Momentum Scoring**
   - Input: 24h price change (%), volume spike boolean
   - Formula: `base = clamp(change_pct / 10, -1, 1)`, if volume spike: `base *= 1.3`, clamp to [-1, 1]
   - Output: float in [-1, 1]. Positive = bullish, negative = bearish.
   - Implementation: `_momentum_score(change_pct, volume_spike) -> float`
7. **Output Schema** — `MarketSignal` Pydantic model:
   ```
   symbol: str                    # e.g. "BTC/USDT"
   price: float                   # last traded price
   change_pct_24h: float          # 24h percentage change
   volume_usd_24h: float          # 24h quote volume in USD
   volume_spike: bool             # true if >2x rolling avg
   momentum_score: float          # [-1, 1] directional score
   timestamp: datetime            # UTC scan time
   ```
8. **Ranking** — Signals sorted by `abs(momentum_score)` descending. Top N returned (default: 10, configurable via `top_movers_count`).
9. **Failure Behavior**
   - Exchange connection error → return empty list → engine skips analysis → no trades
   - Individual ticker parse error → skip that symbol → continue scanning
   - No qualifying signals → return empty list → logged as normal
10. **Extension Points**
    - Add regime detection (TREND_UP, TREND_DOWN, RANGE, CHOP) based on ADX + directional indicators
    - Add cross-exchange scanning by injecting multiple exchange clients
    - Add order book depth analysis for liquidity scoring
11. **Mock/Simulated Behavior** — In paper mode, scanner still calls real Bitget API for market data. Only execution is simulated. This means scans reflect real market conditions even in simulation.

**Key Diagrams:**
- Flowchart: Fetch Tickers → Filter USDT → Volume Check → Momentum Score → Rank → Return Top N
- Volume spike detection logic diagram

**Screenshots / Evidence:**
- Sample `MarketSignal` JSON output from `demo/sample_output.json`

**Common Mistakes:**
- Not clarifying that perception uses real data even in paper mode
- Not documenting the volume spike warmup period (needs 5 observations)
- Not explaining why momentum_score is clamped to [-1, 1]

---

## 3.6 — docs/modules/decision-engine.md

**Purpose:** How the system generates trade hypotheses from raw signals. The "thinking" layer.

**Audience:** Technical reviewers, AI/ML evaluators, developers extending analysis.

**Section Outline:**
1. **Role in the Pipeline** — Decision is stage 2. It answers: "Given what the market is doing, what should we consider trading and why?"
2. **Implementation** — `bot/core/analyzer.py`, class `Analyzer`
3. **Input** — `MarketSignal` + OHLCV candle array (100 bars, 1h timeframe)
4. **Technical Indicators**
   - **RSI-14**: Relative Strength Index over last 14 periods. Oversold <35, overbought >70.
     - Implementation: delta → gain/loss split → avg_gain/avg_loss → RS → RSI
   - **MACD (12, 26, 9)**: EMA-12 minus EMA-26, signal line = EMA-9 of MACD.
     - Bullish: MACD crosses above signal. Bearish: below.
   - **Bollinger Bands (20, 2)**: SMA-20 ± 2 standard deviations.
     - Price near lower band = potential support. Upper band = resistance.
   - **ATR (14)**: Average True Range proxy from close-to-close differences.
     - Used for stop-loss and take-profit distance calculation.
5. **LLM Reasoning Layer**
   - Provider: OpenAI-compatible API (default: GPT-4o)
   - Prompt structure: Asset context + price + indicators → ask for DIRECTION, CONFIDENCE, REASONING
   - Temperature: 0.3 (conservative, reproducible)
   - Output parsing: line-by-line extraction of structured fields
   - Error handling: on LLM failure → fall back to rule-based thesis
6. **Rule-Based Fallback**
   - Activates when: no LLM API key configured, or LLM call fails
   - Logic:
     - RSI < 35 → LONG, RSI > 70 → SHORT, else → LONG
     - Volume spike → confidence 0.6, else → 0.45
   - Reasoning: always includes "rule-based fallback" marker for auditability
7. **Trade Idea Construction**
   - Entry: current price
   - Stop-loss: entry ± 2×ATR (direction-dependent)
   - Take-profit: entry ± 3×ATR → default R:R = 1.5
   - Confidence: from LLM or rule-based, clamped [0, 1]
   - Minimum confidence: 0.5 (below → skip, no idea generated)
   - ID format: `TI-{uuid_hex[:8]}`
8. **Output Schema** — `TradeIdea` Pydantic model:
   ```
   id: str                        # "TI-a1b2c3d4"
   asset: str                     # "BTC/USDT"
   direction: Direction           # LONG or SHORT
   entry_price: float
   stop_loss: float
   take_profit: float
   confidence: float              # [0.0, 1.0]
   reasoning: str                 # LLM or rule-based explanation
   signals_used: list[str]        # ["rsi", "macd", "bb_upper", ...]
   timestamp: datetime
   + computed: risk_reward_ratio   # reward / risk
   ```
9. **Explainability** — Every TradeIdea carries:
   - The indicators that informed it (`signals_used`)
   - The LLM reasoning or rule-based logic (`reasoning`)
   - The confidence score
   - Whether LLM or fallback was used (visible in reasoning text)
10. **Failure Behavior**
    - <30 candles → return None → logged as SKIP
    - LLM error → fallback to rule-based → still produces idea
    - Confidence <0.5 → return None → logged as SKIP
11. **Mock/Simulated Behavior** — Analyzer uses real market data for indicators. LLM calls are real API calls. Only execution is simulated. Without an LLM key, rule-based mode is fully deterministic.

**Key Diagrams:**
- Indicator computation flow: OHLCV → RSI + MACD + BB + ATR → indicator dict
- LLM integration: indicators + signal → prompt → parse response → TradeIdea
- Decision tree: enough candles? → compute indicators → LLM or fallback → confidence check → emit or skip

**Screenshots / Evidence:**
- Sample TradeIdea JSON
- Sample LLM prompt (exact format from code)

**Common Mistakes:**
- Not explaining the fallback mechanism — judges need to know it works without an API key
- Not documenting the 0.5 confidence threshold and its effect
- Presenting ATR as "true range" when implementation is close-to-close (acknowledge the proxy)

---

## 3.7 — docs/modules/risk-engine.md

**Purpose:** The most important module for trust. Show judges that RUNECLAW cannot lose control.

**Audience:** Risk-aware reviewers, judges evaluating safety, developers extending risk checks.

**Section Outline:**
1. **Design Principle** — Fail-closed: if any check cannot be evaluated, the trade is REJECTED. No exceptions. No overrides. The risk engine is a gate, not an advisor.
2. **Implementation** — `bot/risk/risk_engine.py`, class `RiskEngine`
3. **The 7 Pre-Trade Checks**

   | # | Check | Threshold | Trigger |
   |---|-------|-----------|---------|
   | 1 | Circuit Breaker | Boolean | If tripped → REJECT all trades |
   | 2 | Position Size | ≤2% of equity | Per-trade exposure limit |
   | 3 | Daily Loss | <5% of balance | Cumulative daily realized loss |
   | 4 | Max Drawdown | <10% from peak | Peak-to-trough equity decline |
   | 5 | Open Positions | <5 concurrent | Diversification / exposure cap |
   | 6 | Risk/Reward Ratio | ≥1.5 | Reward must exceed risk by 50% |
   | 7 | Confidence Score | ≥0.5 | Minimum conviction threshold |

4. **Evaluation Flow**
   ```
   TradeIdea → evaluate()
     → snapshot portfolio state
     → run check 1 (circuit breaker)
     → run check 2 (position size)
     → run check 3 (daily loss) — may trip circuit breaker
     → run check 4 (drawdown) — may trip circuit breaker
     → run check 5 (open positions)
     → run check 6 (R:R ratio)
     → run check 7 (confidence)
     → if ANY failed → REJECTED
     → if ALL passed → APPROVED
   ```
5. **Circuit Breaker**
   - Trips automatically when daily loss ≥5% or drawdown ≥10%
   - Once tripped: ALL future trades are rejected until manual reset
   - Reset: `reset_circuit_breaker()` — requires human intervention (no auto-reset)
   - Rationale: prevents compounding losses during adverse conditions
6. **Re-Check on Confirmation**
   - When a human confirms a pending trade, risk is re-evaluated
   - Market conditions may have changed between idea generation and confirmation
   - If re-check fails → trade is rejected even though human confirmed
   - This is a critical safety feature — time gap between idea and action is a real risk
7. **Output Schema** — `RiskCheck` Pydantic model:
   ```
   trade_id: str
   verdict: RiskVerdict            # APPROVED or REJECTED
   position_size_usd: float
   position_pct: float
   daily_loss_pct: float
   drawdown_pct: float
   checks_passed: list[str]        # human-readable pass messages
   checks_failed: list[str]        # human-readable fail reasons
   reason: str                     # summary of failure(s) or "All checks passed"
   timestamp: datetime
   ```
8. **Audit Integration** — Every `evaluate()` call is logged to `risk.jsonl` with:
   - Full `RiskCheck` model dump
   - Verdict (APPROVED/REJECTED)
   - Circuit breaker state changes
9. **Failure Behavior**
   - Portfolio state unavailable → equity=0 → position size = 100% → REJECTED (fail-closed)
   - Idea missing R:R fields → ratio = 0 → below 1.5 → REJECTED
   - Any exception in evaluate → caught at engine level → trade not executed
10. **Extension Points**
    - Correlation check (avoid concentrated bets in correlated assets)
    - Volatility regime filter (don't trade in CHOP regime)
    - Time-of-day restrictions
    - Consecutive loss streaks
    - Exposure-weighted position sizing

**Key Diagrams:**
- Risk check flowchart (18 checks in sequence, any fail → REJECTED)
- Circuit breaker state machine (OK → TRIPPED → manual RESET → OK)
- Timeline: idea generated → time passes → human confirms → re-check → execute or reject

**Screenshots / Evidence:**
- Sample `RiskCheck` JSON from `demo/sample_risk_check.json`
- Example of a REJECTED trade with specific check failures

**Common Mistakes:**
- Not emphasizing fail-closed default — this is the key differentiator
- Not explaining re-check on confirmation — judges may miss this
- Not showing what happens when circuit breaker trips (all trades blocked, manual reset required)
- Treating risk as advisory instead of mandatory gate

---

## 3.8 — docs/modules/execution.md

**Purpose:** Clarify the execution boundary — what actually happens when a trade is confirmed. Make the paper/live separation crystal clear.

**Audience:** Judges evaluating safety, developers understanding the execution path.

**Section Outline:**
1. **Execution Path** — The only path to execution:
   ```
   Human confirms via Telegram → Engine.confirm_trade()
     → Re-check risk → [APPROVED]
       → CONFIG.is_live()? → [False] → Paper execution
                            → [True]  → "LIVE TRADING IS DISABLED" message
   ```
2. **Paper Execution** — `PortfolioTracker.open_position(idea, size_usd)`
   - Deducts size from paper balance
   - Creates `TradeExecution` record with entry price, SL, TP
   - Position tracked in memory, monitored for SL/TP hits
3. **Position Monitoring** — `Engine._check_open_positions()`
   - Runs every scan cycle
   - Fetches current prices from Bitget
   - Checks each open position for SL/TP breach
   - Auto-closes positions that hit stops
   - PnL calculated and recorded
4. **Live Execution Gate** — `CONFIG.is_live()` requires BOTH:
   - `LIVE_TRADING_ENABLED=true`
   - `SIMULATION_MODE=false`
   - Currently: live execution returns a message saying it's disabled
   - This is intentional for hackathon scope
5. **Execution Adapter Pattern** (architecture, not yet implemented)
   - `PaperExecutor` — current implementation
   - `BacktestExecutor` — replay historical data
   - `DryRunExecutor` — log what would happen without state changes
   - `LiveExecutor` — Bitget order API (future)
6. **Output Schema** — `TradeExecution`:
   ```
   trade_id: str
   asset: str
   direction: Direction
   entry_price: float
   quantity: float
   stop_loss: float
   take_profit: float
   status: TradeStatus             # PENDING → EXECUTED → CLOSED
   pnl: float                     # 0.0 until closed
   exit_price: float | None
   is_paper: bool                 # always True in current build
   opened_at: datetime
   closed_at: datetime | None
   ```
7. **Mock/Simulated/Production Separation**
   - **Mock**: CLI mode with no exchange connection — portfolio operations work, no real prices
   - **Simulated**: Paper mode with real Bitget data — full pipeline, no real orders
   - **Production-capable**: Architecture supports live execution, but code path returns disabled message

**Key Diagrams:**
- Execution decision tree: confirm → re-check → is_live? → paper vs disabled
- Position lifecycle: OPEN → MONITORING → SL/TP HIT → CLOSED → PnL recorded

**Screenshots / Evidence:**
- Sample portfolio state from `demo/sample_portfolio.json`

**Common Mistakes:**
- Not making the paper/live boundary explicit — judges need to see this is safe
- Not explaining that `is_live()` requires TWO flags (defense in depth)
- Presenting live execution as "coming soon" without acknowledging current state

---

## 3.9 — docs/modules/memory-and-journal.md

**Purpose:** Explain the audit and memory layer — how RUNECLAW remembers and how decisions are reviewed.

**Audience:** Judges evaluating auditability, developers building on the journal.

**Section Outline:**
1. **Logging Architecture** — Three structured JSON channels:
   | Channel | File | Purpose | Writers |
   |---------|------|---------|---------|
   | `runeclaw.trade` | `logs/trade.jsonl` | Trade ideas, executions, confirmations, rejections | Engine, Analyzer, Portfolio |
   | `runeclaw.risk` | `logs/risk.jsonl` | Risk checks, circuit breaker events | RiskEngine |
   | `runeclaw.system` | `logs/system.jsonl` | Scan results, errors, lifecycle events | Scanner, Engine |
2. **Log Entry Schema**
   ```json
   {
     "ts": "2025-06-15T14:30:00.000Z",
     "level": "INFO",
     "channel": "runeclaw.trade",
     "message": "Trade idea: LONG BTC/USDT",
     "action": "analyze",
     "reasoning": "RSI oversold + volume spike",
     "result": "IDEA",
     "data": { ... full TradeIdea model dump ... }
   }
   ```
3. **The `audit()` Function** — Central logging interface:
   ```python
   audit(channel, message, *, action="", reasoning="", result="", data=None, level=INFO)
   ```
   - Every caller must provide `action` (what) and `result` (outcome)
   - Optional `reasoning` for explainability
   - Optional `data` for structured payload (Pydantic model dumps)
4. **What Gets Logged**
   - Scanner: scan start, scan complete (count), scan errors
   - Analyzer: analysis start, indicator computation, LLM call, idea generation, skips
   - Risk Engine: every check evaluation (passed list, failed list), circuit breaker trips/resets
   - Engine: trade confirmation, rejection, execution, position auto-close
   - System: engine start/stop, tick errors, skill registration
5. **Post-Mortem Capability** — With structured JSONL, any trade can be reconstructed:
   - Filter by `trade_id` across all three channels
   - See: signal → idea → risk check → confirmation → execution → close → PnL
   - Machine-readable for automated analysis
6. **Memory Layer** (current state and roadmap)
   - Current: in-memory portfolio state, trade history list, daily PnL dict
   - Roadmap: persistent journal database, trade pattern recognition, strategy memory
7. **Extension Points**
   - SQLite/PostgreSQL persistence for trade history
   - Elasticsearch/Loki for log aggregation
   - Performance analytics dashboard from JSONL data
   - Strategy learning from historical trade outcomes

**Key Diagrams:**
- Log flow: Component → `audit()` → `_JSONFormatter` → file + stderr
- Trade reconstruction: filter JSONL by trade_id → full decision timeline

**Screenshots / Evidence:**
- Sample JSONL entries from each channel
- Reconstructed trade timeline example

**Common Mistakes:**
- Not showing the actual JSON format — judges want to see the structure
- Not explaining that logging is dual-output (file + stderr)
- Calling it "memory" when current implementation is ephemeral — be honest about scope

---

## 3.10 — docs/backtesting.md

**Purpose:** Document the backtesting capability — what exists, what's planned, and how it connects to the main pipeline.

**Audience:** Quantitative reviewers, developers extending the system.

**Section Outline:**
1. **Design Philosophy** — Same pipeline, different data source. Backtesting should use the identical perception → decision → risk → execution path as live/paper trading, with only the data source and executor swapped.
2. **Current State** — Backtesting is architecturally planned but not yet implemented as a standalone mode. The pipeline components (scanner, analyzer, risk engine, portfolio tracker) are all testable with historical data.
3. **Planned Architecture**
   ```
   Historical OHLCV Data → BacktestScanner (replays candles)
     → Analyzer (same as live)
       → RiskEngine (same as live)
         → BacktestExecutor (records fills without confirmation gate)
           → BacktestPortfolio (tracks PnL over time series)
   ```
4. **Key Design Decisions**
   - Confirmation gate is bypassed in backtest mode (no human in the loop for historical replay)
   - Risk engine runs identically — same 18 checks, same circuit breaker
   - Slippage and commission modeling should be configurable
   - Walk-forward validation preferred over simple backtest
5. **Expected Output** — `BacktestResult` model (from Implementation Blueprint):
   - Total trades, win rate, max drawdown, Sharpe ratio, profit factor
   - Equity curve data points
   - Per-trade log with entry/exit/PnL
6. **How to Extend** — Steps to implement backtesting:
   - Create `BacktestScanner` that reads from CSV/database instead of API
   - Create `BacktestExecutor` that skips confirmation
   - Create CLI command: `python -m bot.main --mode backtest --data BTC_1h.csv`
   - Reuse all existing analyzer and risk engine code

**Key Diagrams:**
- Comparison: Live pipeline vs Backtest pipeline (highlight what changes and what stays the same)

**Screenshots / Evidence:** None yet — document as planned feature with clear implementation path.

**Common Mistakes:**
- Claiming backtesting works when it doesn't — be transparent about current state
- Not showing that the architecture supports it — the modularity is the point
- Over-engineering the spec — for a hackathon, the architecture proof is more valuable than a half-baked implementation

---

## 3.11 — docs/paper-trading.md

**Purpose:** Document the paper trading ledger in detail — how positions are tracked, PnL calculated, and portfolio state managed.

**Audience:** Developers, judges evaluating the simulation fidelity.

**Section Outline:**
1. **Default Mode** — RUNECLAW starts in paper trading mode. `SIMULATION_MODE=True`, `LIVE_TRADING_ENABLED=False`. No configuration required.
2. **Implementation** — `bot/risk/portfolio.py`, class `PortfolioTracker`
3. **Starting State** — $10,000 USD paper balance (configurable via `PAPER_BALANCE_USD`)
4. **Position Lifecycle**
   ```
   TradeIdea approved → open_position(idea, size_usd)
     → deduct size from balance
     → create TradeExecution record
     → track in _positions dict
     → monitor each tick for SL/TP
     → SL or TP hit → close_position(trade_id, exit_price)
       → calculate PnL (direction-aware)
       → return size + PnL to balance
       → move to _history list
       → record daily PnL
       → update peak equity
   ```
5. **PnL Calculation**
   - LONG: `(exit_price - entry_price) * quantity`
   - SHORT: `(entry_price - exit_price) * quantity`
   - Quantity: `size_usd / entry_price`
6. **Portfolio Metrics**
   - `balance_usd`: cash not in positions
   - `equity_usd`: balance + value of open positions
   - `open_positions`: count of active trades
   - `total_trades`: count of closed trades
   - `win_rate`: wins / total (0 if no trades)
   - `total_pnl`: sum of all closed trade PnL
   - `daily_pnl`: today's realized PnL
   - `max_drawdown_pct`: peak-to-trough equity decline
7. **Stop-Loss / Take-Profit Monitoring**
   - `check_stops(prices)` runs every engine tick
   - For each open position, checks current price against SL and TP
   - LONG: SL hit if price ≤ stop_loss, TP hit if price ≥ take_profit
   - SHORT: SL hit if price ≥ stop_loss, TP hit if price ≤ take_profit
   - Auto-closes and records PnL
8. **Peak Equity Tracking** — Used for drawdown calculation. Updated on every snapshot and position close. Never decreases (only new highs are recorded).
9. **Limitations**
   - In-memory only — state lost on restart
   - No slippage modeling
   - No commission/fee deduction
   - No partial fills
   - Quantity precision may not match exchange lot sizes
10. **Extending to Persistence** — Swap `PortfolioTracker` for a database-backed implementation. Interface is stable: `open_position()`, `close_position()`, `check_stops()`, `snapshot()`.

**Key Diagrams:**
- Position lifecycle state machine: OPEN → MONITORING → {SL_HIT, TP_HIT} → CLOSED
- Equity waterfall: starting balance → deductions → returns → PnL → final equity

**Screenshots / Evidence:**
- Sample portfolio state JSON from `demo/sample_portfolio.json`
- Example of SL/TP hit log entry

**Common Mistakes:**
- Not acknowledging limitations (no slippage, no fees) — judges prefer honesty
- Not explaining direction-aware PnL calculation
- Not mentioning that state is ephemeral

---

## 3.12 — docs/api.md

**Purpose:** Programmatic reference for all data models, function signatures, and interfaces.

**Audience:** Developers integrating with or extending RUNECLAW.

**Section Outline:**
1. **Data Models** (`bot/utils/models.py`)
   - Full field-by-field documentation for each Pydantic model:
     - `Direction` enum: LONG, SHORT
     - `RiskVerdict` enum: APPROVED, REJECTED
     - `TradeStatus` enum: PENDING, CONFIRMED, EXECUTED, CANCELLED, REJECTED
     - `MarketSignal` — 7 fields with types, defaults, constraints
     - `TradeIdea` — 10 fields + computed `risk_reward_ratio` property
     - `RiskCheck` — 10 fields
     - `TradeExecution` — 12 fields
     - `PortfolioState` — 9 fields
2. **Engine API** (`bot/core/engine.py`)
   - `RuneClawEngine.__init__()` — creates scanner, analyzer, risk engine, portfolio
   - `run()` → async — starts continuous scan loop
   - `stop()` → async — shuts down engine and exchange connection
   - `confirm_trade(trade_id: str) -> str` — human confirmation path
   - `reject_trade(trade_id: str) -> str` — human rejection path
   - `pending_ideas: list[TradeIdea]` — property, read-only
3. **Scanner API** (`bot/core/market_scanner.py`)
   - `MarketScanner.scan() -> list[MarketSignal]` — async
   - `MarketScanner.close()` — async, closes exchange connection
4. **Analyzer API** (`bot/core/analyzer.py`)
   - `Analyzer.analyze(signal: MarketSignal, candles: list[list[float]]) -> Optional[TradeIdea]` — async
5. **Risk API** (`bot/risk/risk_engine.py`)
   - `RiskEngine.evaluate(idea: TradeIdea) -> RiskCheck`
   - `RiskEngine.circuit_breaker_active: bool` — property
   - `RiskEngine.reset_circuit_breaker()` — manual reset
6. **Portfolio API** (`bot/risk/portfolio.py`)
   - `PortfolioTracker.open_position(idea, size_usd) -> TradeExecution`
   - `PortfolioTracker.close_position(trade_id, exit_price) -> Optional[TradeExecution]`
   - `PortfolioTracker.check_stops(prices) -> list[TradeExecution]`
   - `PortfolioTracker.snapshot() -> PortfolioState`
   - `PortfolioTracker.open_positions: list[TradeExecution]` — property
   - `PortfolioTracker.trade_history: list[TradeExecution]` — property
7. **Skill API** (`bot/skills/skill_registry.py`)
   - `BaseSkill` ABC: `name`, `description`, `execute(engine, **kwargs) -> str`
   - `SkillRegistry.register(skill)`, `.get(name)`, `.list_skills()`
   - Built-in skills: `scan_market`, `analyze_asset`, `check_risk`, `execute_paper_trade`, `get_portfolio`, `explain_trade`
8. **Logging API** (`bot/utils/logger.py`)
   - `audit(channel, message, *, action, reasoning, result, data, level)`
   - Channels: `trade_log`, `risk_log`, `system_log`
9. **Configuration** (`bot/config.py`)
   - `AppConfig` dataclass with nested `RiskLimits`, `ExchangeConfig`, `TelegramConfig`, `LLMConfig`
   - `CONFIG` singleton
   - `CONFIG.is_live() -> bool`

**Key Diagrams:** None — this is reference material.

**Screenshots / Evidence:** None — code signatures serve as evidence.

**Common Mistakes:**
- Not including return types
- Not documenting computed properties (`risk_reward_ratio`)
- Not noting which methods are async vs sync
- Not showing the `CONFIG` singleton pattern

---

## 3.13 — docs/demo-guide.md

**Purpose:** Step-by-step script for demonstrating RUNECLAW in 3-5 minutes. Reproducible, no surprises.

**Audience:** The team (for recording/live demo), judges following along.

**Section Outline:**
1. **Pre-Demo Checklist**
   - [ ] Python environment activated
   - [ ] Dependencies installed
   - [ ] `.env` configured (at minimum: Bitget API keys for live scan data)
   - [ ] Terminal font large enough for recording
   - [ ] Telegram bot running (if showing Telegram mode)
2. **Demo Script — CLI Mode** (3 minutes)
   - **0:00 — Launch**
     ```bash
     python -m bot.main --mode cli
     ```
     Show: RUNECLAW banner, `runeclaw>` prompt
   - **0:30 — Portfolio Check**
     ```
     runeclaw> get_portfolio
     ```
     Show: $10,000 paper balance, no open positions
   - **1:00 — Risk Status**
     ```
     runeclaw> check_risk
     ```
     Show: all metrics green, circuit breaker OK
   - **1:30 — Market Scan**
     ```
     runeclaw> scan_market
     ```
     Show: top movers with prices, changes, volume spike flags
   - **2:00 — Asset Analysis**
     ```
     runeclaw> analyze_asset BTC
     ```
     Show: TradeIdea with direction, entry, SL, TP, confidence, reasoning
   - **2:30 — Explain the Pipeline**
     Narrate: "Every trade idea passes through 7 independent risk checks. The circuit breaker monitors daily loss and drawdown. No trade executes without human confirmation."
   - **3:00 — Show Audit Logs**
     ```bash
     cat logs/trade.jsonl | python -m json.tool
     ```
     Show: structured JSON entries for every decision
3. **Demo Script — Telegram Mode** (additional 2 minutes)
   - Open Telegram, send `/help` → show command list
   - Send `/scan` → show market signals
   - Send `/analyze BTC` → show trade idea with inline keyboard
   - Tap "Reject" → show rejection logged
   - Send `/portfolio` → show unchanged balance
   - Send `/risk` → show all-clear
4. **Key Talking Points**
   - "Simulation-first — no real money at risk"
   - "Every decision is logged as structured JSON"
   - "The risk engine is fail-closed — one check fails, trade dies"
   - "Human confirms every trade via Telegram"
   - "Works without any API keys in CLI mode"
5. **Fallback Plan**
   - If Bitget API is down: CLI mode still works for portfolio/risk checks
   - If LLM API is down: rule-based fallback generates ideas
   - If Telegram is unavailable: CLI mode demonstrates the same pipeline

**Key Diagrams:** None — this is a procedure.

**Screenshots / Evidence:**
- Expected terminal output for each command
- Telegram screenshots with inline keyboards

**Common Mistakes:**
- Not having a fallback plan for API outages
- Not rehearsing — the demo should be practiced at least twice
- Showing too many features instead of focusing on the pipeline story
- Not showing the audit logs — this is a major differentiator

---

## 3.14 — docs/faq.md

**Purpose:** Preemptively answer questions judges and developers will have.

**Audience:** Everyone.

**Section Outline:**

### Setup & Running
- **Q: Do I need API keys to run RUNECLAW?**
  A: No. CLI mode works without any API keys. You'll see the paper portfolio ($10,000) and risk metrics. Market scanning requires Bitget API keys. AI analysis requires an OpenAI-compatible API key (or uses rule-based fallback).

- **Q: Does RUNECLAW trade with real money?**
  A: No. Simulation mode is on by default. Live trading requires explicitly setting both `SIMULATION_MODE=false` AND `LIVE_TRADING_ENABLED=true`. Even then, the current code returns a disabled message.

- **Q: What exchange does RUNECLAW support?**
  A: Bitget, via ccxt. The exchange client is abstracted — adding exchanges requires implementing the same ccxt interface.

- **Q: What LLM does RUNECLAW use?**
  A: OpenAI-compatible API, default model `gpt-4o`. Configurable via `LLM_MODEL`. Without an API key, a deterministic rule-based fallback generates trade ideas.

### Architecture & Design
- **Q: What does "fail-closed" mean?**
  A: If any risk check cannot be evaluated or fails, the trade is rejected. The system defaults to "no trade" — not "trade anyway." This is the opposite of fail-open, where errors might allow unauthorized actions.

- **Q: Why does the risk engine re-check on confirmation?**
  A: Time passes between when a trade idea is generated and when a human confirms it. Market conditions may have changed. Re-checking ensures the trade is still valid at execution time.

- **Q: What triggers the circuit breaker?**
  A: Daily realized loss exceeding 5% of balance, or equity drawdown exceeding 10% from peak. Once tripped, all trades are rejected until manual reset.

- **Q: Can I override the risk engine?**
  A: No. By design. The risk engine is a mandatory gate, not an advisory layer.

- **Q: How is this different from other trading bots?**
  A: Most bots are either indicator dashboards with manual execution or black-box auto-traders. RUNECLAW is an agent that perceives, reasons, validates, and — only with permission — acts. Every step is explainable and auditable.

### Evaluation
- **Q: How do I verify the system is working correctly?**
  A: Run `python -m bot.main --mode cli`, type `get_portfolio` (should show $10,000), type `check_risk` (should show all-clear). Check `logs/` for JSONL output.

- **Q: Where are the test files?**
  A: Demo outputs are in `demo/`. The system is designed for integration testing through CLI mode. Unit test files are planned for the `tests/` directory.

- **Q: Is the data real or simulated?**
  A: Market data is real (from Bitget API). Analysis uses real indicators and real LLM calls. Only execution is simulated (paper trading).

---

## 3.15 — docs/submission.md

**Purpose:** Hackathon-specific submission context. What judges should know, what the project demonstrates, and how it maps to evaluation criteria.

**Audience:** Hackathon judges exclusively.

**Section Outline:**
1. **Project Summary**
   - Name: RUNECLAW — AI Trading Command Core
   - Team: Humanoid Traders
   - Track: AI Trading Agent / Autonomous Trading
   - Hackathon: Bitget GetClaw AI Builder Base Camp 2025
2. **What We Built**
   - Autonomous trading agent runtime with structured perception → decision → risk → execution → audit pipeline
   - Simulation-first design (paper trading default)
   - Fail-closed risk engine (18 pre-trade checks, circuit breaker)
   - Human-in-the-loop confirmation (Telegram inline keyboards)
   - Explainable AI (every trade idea includes reasoning, indicators, confidence)
   - Full audit trail (structured JSONL logging)
   - Modular skill system (extensible, composable)
   - Three operational modes: CLI, Telegram, Scan
3. **Bitget Alignment**
   - Built on Bitget API via ccxt
   - Scans all Bitget USDT spot pairs
   - Sandbox mode supported for API testing
   - Architecture maps to Bitget Agent Hub's composable agent paradigm
4. **Innovation Highlights**
   - **Re-check on confirmation**: Risk is re-evaluated at confirmation time, not just at idea generation
   - **Fail-closed default**: Error in risk evaluation = rejection, not pass-through
   - **Circuit breaker with manual-only reset**: Prevents automated recovery from loss spirals
   - **Rule-based fallback**: System functions without LLM API key — no single point of failure
   - **Dual safety flags**: Live trading requires TWO explicit opt-in flags
5. **What We Would Build Next** (demonstrates architectural thinking)
   - Backtesting engine using same pipeline
   - Regime detection (TREND_UP, TREND_DOWN, RANGE, CHOP)
   - Multi-timeframe confluence scoring
   - Persistent trade journal with pattern recognition
   - Live execution adapter with order management
6. **How to Evaluate**
   - Clone and run in CLI mode (zero config, <2 minutes)
   - Review architecture in `docs/architecture.md`
   - Inspect risk engine: `bot/risk/risk_engine.py` (116 lines, all 18 checks visible)
   - Check audit logs: `logs/*.jsonl`
   - Read the agent prompt: `bot/prompts/system_prompt.md`
7. **Links**
   - GitHub: https://github.com/Humanoid-Traders/RUNECLAW
   - Documentation: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
   - Telegram: https://t.me/+VRNgsmkR5pszZTdk

**Key Diagrams:** None — reference other docs.

**Screenshots / Evidence:**
- Link to demo guide
- Link to sample outputs in `demo/`

**Common Mistakes:**
- Not mapping to hackathon evaluation criteria explicitly
- Being vague about "what we built" — be specific about implemented vs planned
- Not providing a clear "how to evaluate" section — make it easy for judges
- Overselling — state limitations honestly, it builds trust

---

# 4. DOCUMENTATION BEST-PRACTICE NOTES

## Writing Standards

1. **Be specific, not impressive.** "7 independent pre-trade risk checks" is better than "comprehensive risk management." Judges have read 50 "comprehensive" submissions.

2. **Show the code path.** For every claim about behavior, reference the file and function. `bot/risk/risk_engine.py:evaluate()` is verifiable. "Our advanced risk system" is not.

3. **Distinguish implemented from planned.** Use present tense for what exists: "The scanner fetches all USDT pairs." Use future tense or "planned" for roadmap: "Backtesting will reuse the same pipeline." Never blur this boundary.

4. **Show failure modes.** Every module doc should have a "Failure Behavior" section. Judges evaluating trading systems look specifically for how errors are handled. "Fails closed" must be demonstrated, not just claimed.

5. **Include sample JSON.** For every data model, show a realistic JSON example. Judges who don't read Python can evaluate the data design from JSON alone.

6. **Use tables for structured comparisons.** Environment variables, features, checks — tables are scannable. Prose is not.

7. **Keep pages independent.** Each doc should make sense on its own. Link to related pages but don't require reading them in order.

## Structural Rules

8. **One topic per page.** Risk engine gets its own page. Don't combine it with execution. The sidebar should act as a table of contents.

9. **Section order within each page:**
   - Role / Purpose (1 paragraph)
   - Implementation reference (file + class)
   - How it works (the substance)
   - Output schema (what it produces)
   - Failure behavior (what goes wrong)
   - Extension points (what could be added)
   - Mock/simulated/production separation

10. **Diagrams:** ASCII for inline (works in any renderer). Mermaid for GitBook (renders natively). Never reference external image URLs that may break.

## Judge-Specific Guidance

11. **Front-load the "why."** The first paragraph of every page should answer: "Why does this module exist and what problem does it solve?"

12. **Don't hide the demo.** The demo guide should be linked from the README, the overview, and the submission page. Judges evaluate what they can see running.

13. **Acknowledge scope.** This is a hackathon project. Saying "backtesting is architecturally planned but not yet implemented" is stronger than pretending it's done or hiding its absence.

14. **Show the audit trail.** The structured logging is a key differentiator. Include sample log entries in multiple docs. Make it clear that every decision is traceable.

## Anti-Patterns

15. **Don't write docs that repeat the README.** Each page must add depth the README doesn't have.

16. **Don't use placeholder text.** Every `TODO`, `TBD`, or `Coming soon` in published docs signals incomplete work. Either write the content or omit the section.

17. **Don't over-document config.** The `.env.example` file is self-documenting. The setup page should explain what matters, not list every possible configuration permutation.

18. **Don't create docs for features that don't exist.** If backtesting isn't implemented, the backtesting page should say exactly that and explain the architecture that supports it — not pretend it works.

---

*End of Documentation Architecture — RUNECLAW by Humanoid Traders*
