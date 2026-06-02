```
 ____  _   _ _   _ _____ ____ _        ___        __
|  _ \| | | | \ | | ____/ ___| |      / \ \      / /
| |_) | | | |  \| |  _|| |   | |     / _ \ \ /\ / /
|  _ <| |_| | |\  | |__| |___| |___ / ___ \ V  V /
|_| \_\\___/|_| \_|_____\____|_____/_/   \_\_/\_/
```

<h3 align="center">AI Trading Command Core | Forged in Volatility. Governed by Discipline.</h3>
<h4 align="center">by Humanoid Traders | for Bitget AI Base Camp</h4>
<h5 align="center">🏆 Proudly built for Bitget AI Base Camp · Hackathon S1 – aiming for Best Strategy & Risk Award 🏆</h5>

<p align="center">
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai"><img src="https://img.shields.io/badge/Full_Documentation-%E2%86%92_GitBook-blue?style=for-the-badge&logo=gitbook&logoColor=white" alt="Full Documentation → GitBook"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-AGPL--3.0-blue" alt="License AGPL-3.0">
  <img src="https://img.shields.io/badge/tests-361%20passing-brightgreen" alt="361 Tests Passing">
  <img src="https://img.shields.io/badge/security%20scan-passed-brightgreen" alt="Security Scan Passed">
  <img src="https://img.shields.io/badge/red%20team-100%25-brightgreen" alt="Red Team 100%">
  <img src="https://img.shields.io/badge/security%20tests-29%20passing-blueviolet" alt="29 Security Tests">
  <img src="https://img.shields.io/badge/red%20team-28%20attacks%20%7C%20100%25%20pass-critical" alt="Red Team 100% Pass">
  <img src="https://img.shields.io/badge/risk%20checks-21%20fail--closed-red" alt="21 Risk Checks">
  <img src="https://img.shields.io/badge/mode-paper%20trading-orange" alt="Paper Trading">
  <img src="https://img.shields.io/badge/exchange-Bitget-blue" alt="Bitget">
  <img src="https://img.shields.io/badge/bot-LIVE%20%40HTRUNECLAW__bot-26a5e4?logo=telegram" alt="Live Telegram Bot">
  <img src="https://img.shields.io/badge/hackathon-AI%20Base%20Camp%20S1-purple" alt="AI Base Camp Hackathon S1">
</p>

<p align="center">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW">GitHub</a> &middot;
  <a href="https://xbf5gmte.mule.page/">Website</a> &middot;
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai">Documentation</a> &middot;
  <a href="https://t.me/HTRUNECLAW_bot">Live Bot</a> &middot;
  <a href="https://t.me/+VRNgsmkR5pszZTdk">Telegram</a> &middot;
  <a href="https://x.com/BaurPatric70363">X / Twitter</a>
</p>

<p align="center">
  <b>Try it live right now &rarr; <a href="https://t.me/HTRUNECLAW_bot">@HTRUNECLAW_bot</a></b>
</p>

---

> **DISCLAIMER:** RUNECLAW is an educational prototype built for the Bitget AI Base Camp · Hackathon S1.
> It is **not production-ready** and should **never** be used with real funds without extensive
> additional safeguards, independent security audits, stress testing, and regulatory review.
> Backtest results use synthetic data and do not predict future performance. Past performance
> is not indicative of future results. This project is not financial advice.

---

## What is RUNECLAW?

**RUNECLAW** is an AI trading command system built by **Humanoid Traders** for the Bitget AI Base Camp · Hackathon S1. It merges multi-timeframe analysis, confluence scoring, regime detection, order-flow microstructure, and risk-first logic into a disciplined framework -- all controllable through a Telegram bot interface.

The system operates in **simulation-first mode by default**. Every trade idea must pass twenty-one independent risk checks, an adversarial self-critique gate, and receive explicit human confirmation before execution. An additional liquidity guard runs on live order-flow data when available. No exceptions.

> **Shield risk engine available as MCP server -- any GetClaw agent can call it.** See `bot/mcp/server.py`.

**Key philosophy:** The bot suggests. The human decides. The risk engine enforces.

### AI Learning System (NEW)
RUNECLAW includes a full **self-improving AI learning system** with 8 integrated modules:
- **Experience Memory** -- every trade decision logged with full market context
- **Reflection Engine** -- post-trade analysis generates lessons and improvement proposals
- **Strategy Evaluator** -- risk-adjusted scoring with S/A/B/C/D tier rankings
- **Pattern Learner** -- detects recurring market patterns across regimes
- **Macro Learner** -- tracks crypto reactions to FOMC/CPI/NFP/PCE events
- **Model Comparer** -- side-by-side rule-based vs LLM accuracy tracking
- **Prompt Optimizer** -- version-tracks prompts with performance scoring
- **Feedback Collector** -- integrates human feedback into learning loop

All proposals pass through a **safety policy** with blocked-action lists, risk-increase keyword detection, and change classification. Patterns may NEVER override the risk engine (enforced via Pydantic validator).

### LLM Token Optimizer (NEW)
A 4-layer optimization pipeline reduces LLM API costs by up to 70%:
- **Semantic Cache** -- TTL-bucketed response cache keyed on market regime, RSI zone, MACD direction
- **Tiered Pipeline** -- Tier 1 (free rules) for clear signals, Tier 2 (mini model) for moderate, Tier 3 (full model) for high-potential
- **Smart Batching** -- combines up to 5 symbols per LLM call
- **Adaptive Frequency** -- skips LLM entirely in quiet/low-ADX markets

### Multi-Provider LLM Support (NEW)
RUNECLAW supports any OpenAI-compatible LLM provider via `LLM_BASE_URL`:
- **Google Gemini 2.5 Flash** -- default provider, zero-cost reasoning with free-tier API key
- **Alibaba Qwen** -- `qwen-max`, `qwen-plus`, `qwen-turbo` via DashScope (Hackathon S1 partner)
- **Groq** -- `llama-3.3-70b-versatile` with fastest inference (free tier)
- **OpenRouter** -- `qwen/qwen3.6-35b-a3b` at $0.15/M tokens (cheapest frontier model)
- **Together AI / Fireworks** -- open-source Qwen models with fast inference
- **Local (vLLM/Ollama)** -- self-hosted for zero API cost

> **Zero-cost setup:** Set `LLM_PROVIDER=gemini` and `LLM_MODEL=gemini-2.5-flash` with a free API key from [Google AI Studio](https://aistudio.google.com/apikey). No credit card required.

### Solana Ecosystem Mode (NEW)
Set `ASSET_UNIVERSE=solana` in `.env` or use `/mode solana` in the Telegram bot to prioritize 15 Solana ecosystem tokens. All tokens trade on Bitget with full USDT pair support.

**Tokens:** SOL, JUP, JTO, BONK, WIF, PYTH, RAY, ORCA, RENDER, HNT, MOBILE, W, JITO, TENSOR, DRIFT

**Solana-specific risk tweaks:**
- **Meme-coin volatility guard**: BONK and WIF use a tighter 4% ATR threshold (vs 6% default) to prevent entries during extreme volatility spikes
- **Ecosystem correlation group**: Non-meme Solana tokens (JUP, JTO, PYTH, RAY, etc.) are grouped as `SOLANA_ECO` -- the risk engine limits concentrated bets across correlated assets
- **Live mode switching**: `/mode solana` and `/mode all` switch scanner focus without restart

### Natural Language Interface (NEW)
Talk to RUNECLAW in plain English instead of memorizing commands:
- **Intent routing**: "how's Bitcoin?" dispatches to `/analyze BTC`, "what's moving?" triggers `/scan`
- **Symbol extraction**: understands tickers (`$ETH`), names (`Solana`), and pairs (`BTC/USDT`)
- **LLM fallback**: when rule-based patterns don't match, optional LLM classification routes to the right skill
- **AI chat**: unmatched messages get a contextual response from the LLM (never invents trades)

### Proactive Alert Monitor (NEW)
Background coroutine that pushes unsolicited alerts without waiting for commands:
- **Circuit breaker** trips and clears
- **Volume spikes** on scanned assets
- **Black-swan** anomaly detections
- **Engine state changes** (halt, cooldown)
- **New trade signals** pending confirmation

Toggle with `/watch on|off` per chat. Read-only -- the monitor never creates trades or modifies risk.

### Red Team Stress Tester (NEW)
An adversarial engine that attacks the risk engine with 28 scenarios across 10 categories:
flash crashes, liquidity drains, correlated selloffs, stale data injection, confidence manipulation,
R:R gaming, circuit breaker evasion, zero/negative values, direction inversion, and max position flooding.
Verifies 100% pass rate -- every adversarial scenario correctly caught or approved. Includes ATR=0 bad-data test.

### Adversarial Self-Critique Gate (NEW)
Pre-trade bear-case analysis that runs on every confirmed trade before execution:
- 7 heuristic checks: overconfidence (>90%), marginal R:R (<1.5x), directional crowding (3+ same-direction), same-asset double-down, portfolio heat (4+ open), macro headwind, tight stop (<1% from entry)
- **HALT** verdict at 3+ concerns blocks execution with full explanation
- **WARN** verdict logs concerns but allows trade to proceed
- Fail-open design: critique errors never block trades (unlike fail-closed risk engine)

### Portfolio Value at Risk (NEW)
Parametric VaR as risk check #21:
- 95% confidence interval using historical per-trade return volatility
- Rejects trades pushing portfolio VaR above 15% of equity (configurable via `MAX_PORTFOLIO_VAR_PCT`)
- Gracefully skips with fewer than 5 closed trades (insufficient history)

### Cryptographic Attestation (NEW)
Ed25519 digital signatures for audit chain non-repudiation:
- Merkle root computed over batch of audit entry hashes
- Signed with Ed25519 private key (generated on first run, stored at `data/attestation_key.bin`)
- Verify any batch against the public key to prove entries were created by this bot instance
- Graceful fallback: if `cryptography` package is missing, SHA-256 hash chain still operates

### Black Swan Detector (NEW)
Statistical anomaly detection that pre-empts the circuit breaker. Monitors 5 anomaly types:
correlation breakdown, volume collapse, price acceleration (flash crash), volatility explosion (ATR spike),
and bid-ask spread widening. Triggers pre-emptive halts BEFORE the circuit breaker's 5% daily loss threshold fires.

### Sentiment Fusion Engine (NEW)
Real-time sentiment as the 11th confluence voter. Combines:
- **Fear & Greed Index** (0-100) from price momentum (40%), volume trend (30%), volatility (30%)
- **Contrarian logic**: extreme fear -> bullish vote [+0.3, +0.6], extreme greed -> bearish vote [-0.6, -0.3]
- **Funding-rate contrarian**: extreme positive funding adds bearish offset, extreme negative adds bullish

### Multi-Agent Swarm Protocol (NEW)
Composable agent collaboration via MCP-compatible architecture. Five specialized agents:
Scanner (perceives market), Analyst (generates theses), Risk (gates every trade), Executor (manages positions),
Sentinel (monitors for black swans). Communication via SwarmBus pub/sub, with Sentinel broadcasting HALT
to all agents when severity >= 0.8. Ready for production deployment as separate Agent Hub agents.

---

## Architecture

```
 Telegram Bot                       Bitget Exchange
      |                                   |
      v                                   v
 +-----------+    +---------------+   +-----------+
 |  Skill    |--->|  RuneClaw     |-->|  Market   |
 |  Registry |   |  Engine       |   |  Scanner  |
 +-----------+    +-------+-------+   +-----------+
                          |                 |
                   +------+------+    OHLCV / Tickers
                   |             |
              +----v----+  +----v-----+
              |   AI    |  |  Risk    |
              | Analyzer|  |  Engine  |
              +---------+  +----+-----+
                   |            |
              Trade Idea   Risk Check
                   |            |
                   v            v
              +----+------------+----+
              | Human Confirmation   |
              | (Telegram Keyboard)  |
              +----------+-----------+
                         |
                    +----v----+
                    |Portfolio|
                    | Tracker |
                    +---------+
```

**Pipeline:** SCAN --> ANALYZE --> RISK GATE --> HUMAN CONFIRM --> EXECUTE (paper)

---

## Features

### Market Intelligence
- Real-time scanning of all Bitget USDT pairs
- Volume spike detection (2x rolling average)
- Momentum scoring with configurable thresholds
- Top N mover ranking with structured signal output

### AI Analysis Engine
- Technical indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR-14, ADX-14, VWAP, SMA-50 trend alignment, On-Balance Volume (OBV), Rolling VWAP (20-bar and 50-bar)
- Candlestick pattern detection: 14 patterns including doji, hammer, shooting star, engulfing, harami, tweezer top/bottom, morning/evening star, three white soldiers, three black crows
- Fibonacci retracement levels: swing high/low detection over 50-bar lookback, standard levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) with zone classification
- 10-voter confluence scoring model (expanded from 6): RSI, MACD, Bollinger %B, Volume Spike, ADX, VWAP, OBV trend, candlestick pattern, Fibonacci zone, plus LLM confidence
- LLM-powered directional thesis generation (Gemini 2.5 Flash default, GPT-4o / Anthropic / Groq compatible)
- Rule-based fallback when no LLM key is configured
- Structured `TradeIdea` output with entry, SL, TP, confidence, reasoning

### Smart Money Engine (NEW)
- **Liquidation cascade detection** -- funding rate extremes + OI changes + CVD divergence signal crowded-trade liquidation risk
- **Funding rate squeeze** -- contrarian positioning detector with rolling momentum tracking
- **Whale flow tracking** -- rolling buy/sell history with stealth accumulation detection and consistency amplifier
- **Composite scoring** -- weighted blend (institutional 35%, contrarian 20%, whale 25%, cascade 20%) normalized to [-1, 1]
- Thread-safe rolling state with bounded memory

### Multi-Timeframe Analysis (NEW)
- **HTF trend alignment** across 1H/4H/1D using EMA20 vs EMA50
- **Market structure detection** -- swing highs/lows, HH/HL (bullish), LH/LL (bearish)
- **Break of Structure (BOS)** -- price beyond last swing point
- **Change of Character (CHoCH)** -- structural reversal detection
- Alignment scoring with conflicting timeframe penalty
- Graceful fallback when HTF data unavailable

### Adaptive Strategy Modes (NEW)
- **5 strategy modes** selected based on regime + context:
  - TREND_CONTINUATION: wide TP (R:R 2.0), HTF alignment required
  - BREAKOUT: high confidence bar (0.65), requires BOS + volume
  - MEAN_REVERSION: tight SL/TP, RSI/BB extremes, CVD divergence
  - LIQUIDITY_SWEEP: highest confidence bar (0.68), cascade + whale confirmation
  - CONSERVATIVE: default/uncertain, standard parameters
- Per-mode SL/TP multipliers, minimum confidence, and confluence boosts
- Mode selection is audited and explained

### Explainability Engine (NEW)
- **Structured reasoning chains** -- step-by-step logic from data collection to risk assessment
- **Factor attribution** -- per-indicator contribution percentages with top bullish/bearish factors
- **Compliance scoring** -- explainability, data sufficiency, risk documentation, audit trail
- **Natural language narratives** -- one-line summary for Telegram, detailed multi-paragraph for audit
- Designed to support MiCA-style decision auditability

### Risk Engine (Fail-Closed)
- **21 independent pre-trade checks** -- all fail-closed (one failure = rejection), including liquidity guard, macro event gate, multi-timeframe alignment, and concentration PCA
- Circuit breaker halts trading on daily loss or drawdown breach
- Fixed-fractional position sizing: risk budget (2% of equity) divided by stop distance, capped at 20% notional
- Max open positions limit
- Risk/reward ratio minimum (1.2x)
- Confidence threshold gate (≥60%)
- Per-symbol exposure limit (20% max per asset)
- Correlation group concentration guard
- Consecutive loss streak detection + cooldown
- Stale data guard (rejects ideas >5min old)
- Volatility guard (ATR-based)
- Re-check on confirmation (market may have moved)

### Paper Trading
- Full portfolio tracking with PnL, win rate, and drawdown
- Automatic stop-loss and take-profit monitoring
- Trade history ledger for post-mortem analysis
- $10,000 default paper balance (configurable)

### Telegram Bot Interface
- Slash commands for every operation
- Inline keyboard for trade confirmation/rejection
- Per-user rate limiting (20 req/min)
- Real-time status and risk dashboard

### Audit Trail
- Structured JSON logging (JSONL format)
- Three channels: `trade.jsonl`, `risk.jsonl`, `system.jsonl`
- Every decision, confirmation, and rejection is recorded
- Machine-readable for post-hackathon analysis

---

## Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r bot/requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env with your API keys

# 5. Run in CLI mode (no Telegram token needed)
python -m bot.main --mode cli

# 6. Run with Telegram bot
python -m bot.main --mode telegram

# 7. One-shot market scan
python -m bot.main --mode scan
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/scan` | Scan market for top movers and volume spikes |
| `/analyze BTC` | Run AI analysis on a specific asset |
| `/portfolio` | View paper portfolio summary |
| `/trade` | View and confirm/reject pending trades |
| `/risk` | Risk metrics and circuit breaker status |
| `/rejected` | Recent risk-rejected trades with failure reasons |
| `/backtest` | Run backtest with synthetic data |
| `/status` | Bot mode, equity, open positions |
| `/halt` | Emergency kill-switch (trip breaker, cancel all) |
| `/learn` | AI learning system dashboard |
| `/patterns` | View detected market patterns |
| `/proposals` | View pending improvement proposals |
| `/optimize` | LLM token optimization stats |
| `/mode solana` | Switch to Solana ecosystem mode (15 tokens) |
| `/mode all` | Switch back to all Bitget markets |
| `/help` | List all available commands |

Trade confirmation uses Telegram inline keyboards -- tap **Confirm** or **Reject** directly in the chat.

---

## Project Structure

```
runeclaw/
|-- bot/
|   |-- main.py                 # Entry point (telegram / cli / scan / backtest)
|   |-- config.py               # All settings from env, fail-closed defaults
|   |-- core/
|   |   |-- engine.py           # Central orchestrator (9-state FSM)
|   |   |-- market_scanner.py   # Bitget market scanner, volume spike detection
|   |   |-- analyzer.py         # AI + technical analysis, 10+ voter confluence
|   |   |-- order_flow.py       # Exchange microstructure: CVD, book imbalance, whales
|   |   |-- smart_money.py      # Liquidation cascade, funding squeeze, whale tracking
|   |   |-- multi_timeframe.py  # HTF alignment, market structure, BOS/CHoCH
|   |   |-- strategy_modes.py   # 5 adaptive strategy modes with per-mode configs
|   |   |-- red_team.py         # 28-scenario adversarial stress tester
|   |   |-- black_swan.py       # Statistical anomaly detection (5 anomaly types)
|   |   |-- sentiment.py        # Sentiment fusion engine (11th confluence voter)
|   |   |-- swarm.py            # Multi-agent swarm protocol (MCP-compatible)
|   |   |-- explainability.py   # Reasoning chains, factor attribution, compliance
|   |   |-- ta_utils.py         # Shared TA utilities (EMA, ADX, Regime)
|   |   |-- metrics.py          # Sharpe/Sortino/Calmar from per-trade returns
|   |   |-- llm_cache.py        # Semantic LLM response cache with TTL
|   |   |-- token_optimizer.py  # Tiered pipeline, smart batching, adaptive frequency
|   |-- risk/
|   |   |-- risk_engine.py      # 21-check risk gate, circuit breaker
|   |   |-- portfolio.py        # Paper trading ledger, PnL tracking, mark-to-market
|   |-- learning/
|   |   |-- orchestrator.py     # 10-step learning workflow coordinator
|   |   |-- experience.py       # Decision memory and trade history
|   |   |-- reflection.py       # Post-trade reflection and lesson extraction
|   |   |-- strategy_eval.py    # Risk-adjusted strategy scoring (S/A/B/C/D tiers)
|   |   |-- patterns.py         # Recurring pattern detection
|   |   |-- macro_learner.py    # Macro event reaction tracking
|   |   |-- model_compare.py    # Rule-based vs LLM accuracy comparison
|   |   |-- prompt_opt.py       # Prompt version tracking and optimization
|   |   |-- feedback.py         # Human feedback collection
|   |   |-- safety_policy.py    # Immutable safety rules, blocked actions
|   |   |-- store.py            # JSON-based learning data persistence
|   |   |-- models.py           # Pydantic models for all learning records
|   |-- macro/
|   |   |-- calendar.py         # 2026 FOMC/CPI/NFP/PCE event calendar
|   |   |-- models.py           # Macro event and risk state models
|   |-- skills/
|   |   |-- skill_registry.py   # Modular skill system, built-in skills
|   |   |-- telegram_handler.py # Telegram bot commands, inline keyboards
|   |-- backtest/
|   |   |-- engine.py           # Backtest engine with intrabar SL/TP + walk-forward
|   |   |-- data_loader.py      # Synthetic data (GBM + GARCH), CSV, Bitget fetch
|   |   |-- models.py           # Backtest data models
|   |-- utils/
|   |   |-- models.py           # Pydantic schemas (TradeIdea, RiskCheck, etc.)
|   |   |-- trailing.py         # Shared trailing-stop logic
|   |   |-- logger.py           # Structured JSON audit logging
|   |-- prompts/
|   |   |-- system_prompt.md    # Agent persona and capabilities
|   |   |-- skill_definitions.yaml
|   |-- requirements.txt
|-- tests/
|   |-- test_core.py            # 361 pytest tests
|   |-- test_token_optimizer.py # 36 token optimizer tests
|-- docs/
|   |-- gitbook/                # Full GitBook documentation
|   |-- SUBMISSION.md           # Hackathon submission document
|-- demo/
|   |-- sample_output.json      # Example trade idea
|   |-- sample_risk_check.json  # Example risk check
|   |-- sample_portfolio.json   # Example portfolio state
|-- website/
|   |-- index.html              # Landing page
|   |-- dashboard-pro.html      # 3-tab command center dashboard
|-- .env.example
|-- pyproject.toml
|-- Dockerfile
|-- LICENSE
|-- README.md
```

---

## Safety and Risk

RUNECLAW is designed with a **fail-closed** philosophy:

- **Simulation by default.** Live trading requires two explicit environment flags.
- **Every trade passes 21 checks.** All fail-closed (one failure = rejection). No overrides.
- **Circuit breaker.** Auto-halts on daily loss (5%) or max drawdown (10%).
- **Human-in-the-loop.** No trade executes without explicit confirmation.
- **Re-check on confirm.** Risk is re-evaluated at confirmation time because market conditions change.
- **Full audit trail.** Every decision is logged as structured JSON for review.
- **No silent failures.** Unhandled errors abort the pipeline, never proceed.

> **This system is built for hackathon demonstration and paper trading.
> It is NOT financial advice and should NOT be used with real funds without
> extensive additional safeguards, testing, and regulatory review.**

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| Exchange | Bitget via [ccxt](https://github.com/ccxt/ccxt) |
| AI / LLM | Gemini 2.5 Flash default (GPT-4o, Anthropic, Groq configurable) |
| Technical Analysis | NumPy + custom indicators |
| Data Models | Pydantic v2 (strict validation) |
| Bot Interface | python-telegram-bot 20.x |
| Logging | Structured JSON (JSONL) |
| Config | python-dotenv + dataclass defaults |

---

## Security

- **Never commit `.env` files.** The `.env` file contains API keys and secrets. It is listed in `.gitignore`.
- **Rotate API keys regularly.** If you suspect a key has been exposed, revoke it immediately on the Bitget and OpenAI dashboards.
- **Use read-only API keys** for market data operations. Only enable trade permissions if you explicitly intend to go live (not recommended for this prototype).
- **Telegram bot token** grants full control of the bot. Keep it secret. Restrict `TELEGRAM_CHAT_ID` to your own chat ID.
- **LLM API costs:** Each `/analyze` call consumes LLM tokens. Default is Gemini 2.5 Flash (free tier available). GPT-4o costs approximately $0.01-0.03 per analysis. Set `LLM_API_KEY=` (blank) to use the free rule-based fallback instead.
- **No secrets in code.** All credentials load from environment variables with safe defaults. The codebase has been audited to confirm zero hardcoded secrets.

### Security Hardening (Audit v3.0)

| Fix | Category | Description |
|-----|----------|-------------|
| C1 | Critical | Replaced `object.__setattr__` on frozen CONFIG with thread-safe `RuntimeState` wrapper |
| C3 | Critical | Added log redaction layer -- API keys, secrets, tokens stripped from all log output and tracebacks |
| C5 | Critical | MCP server requires bearer token authentication when `MCP_AUTH_TOKEN` is set |
| W1 | Warning | CostTracker now resets daily at UTC boundary; separate `snapshot_lifetime()` for cumulative stats |
| W5 | Warning | Cache keys use full 64-char SHA-256 hex (was truncated to 16) |
| W6 | Warning | Walk-forward backtest cleans up temp directories after each fold |
| Input | Hardening | `/approve` validates numeric Telegram IDs; `/analyze` rejects non-alphanumeric symbols |
| Encapsulation | Hardening | Risk engine uses `portfolio.get_position_value()` public API instead of private `_last_prices` |
| AGPL | Compliance | `/start` and `/help` include source repository link and financial disclaimer |
| Corruption | Hardening | Portfolio logs CRITICAL alert on corrupted state files instead of silent fallback |

**29 dedicated security tests** in `tests/test_security.py` covering: log redaction, MCP auth, runtime state, cache keys, cost reset, portfolio corruption, input validation, and injection prevention.

---

## Limitations and Maturity

This is a **hackathon prototype** (maturity: early-stage). Known limitations:

- **Solo developer project** -- limited peer review beyond automated audits
- **No live trading validation** -- all testing uses paper trading and synthetic data
- **API latency and slippage** -- real exchange conditions differ from simulation
- **Security audit conducted** -- AI-assisted deep audit (v3.0) with all 5 critical issues fixed, 29 security tests added
- **LLM dependency** -- AI analysis quality depends on model availability and cost
- **No guaranteed uptime** -- no monitoring, alerting, or failover infrastructure
- **Scalability:** Single-instance today — swarm-ready via MCP protocol

---

## Team

| Role | Name |
|------|------|
| Lead Developer | *P.Baur* |
| AI / Strategy | *Claude + MuleRun + RUNECLAW* |
| Risk / Backend | *OPEN POSITION* |

---

## RUNECLAW vs Typical Trading Bots

| Capability | RUNECLAW | Typical Hackathon Bot |
|------------|:--------:|:---------------------:|
| Pre-trade risk checks | **21 independent checks** | 0-3 basic checks |
| Fail-closed design | **Yes** -- any failure = rejection | Fail-open (errors skip checks) |
| Circuit breaker | **Auto-halt** on daily loss / drawdown | None or manual only |
| Human confirmation | **Required** via Telegram keyboard | Auto-execute or no gate |
| Regime detection | **ADX-14 regime filter** blocks counter-trend | Not considered |
| Confluence scoring | **10-voter model** (RSI, MACD, BB, Vol, ADX, VWAP, OBV, candles, Fib, LLM) | 1-2 indicators |
| Audit trail | **Full JSONL** -- every decision logged | Minimal or none |
| Simulation-first | **Default mode** -- live requires 2 explicit flags | Often live by default |
| Position sizing | **Fixed-fractional** with exposure caps | Fixed lot or % of balance |
| Re-check on confirm | **Yes** -- market may have moved | No re-validation |
| Backtest engine | **Built-in** with commission + slippage modeling | External or none |
| Live market validation | **324 pairs scanned**, 3 assets analyzed on real Bitget data | Mock data only |

> RUNECLAW doesn't just generate trades -- it governs them. Safety and transparency are first-class features, not afterthoughts.

---

## Fork & Win With Us

RUNECLAW is open for collaboration. If you're building for the Bitget AI Base Camp and want a battle-tested risk engine, scanner, or analysis pipeline -- fork it, extend it, and submit your own entry.

**How to contribute:**

1. **Fork** this repo
2. **Build** your strategy module, UI, or integration on top
3. **Submit** to the hackathon with credit to RUNECLAW as your risk/analysis layer
4. **Open a PR** back with improvements -- we'll merge strong contributions

### Extension Roadmap

| Extension | Description | Difficulty |
|-----------|-------------|------------|
| **Multi-Exchange Connectors** | Add OKX, Bybit, Binance adapters -- same risk engine, more markets | Medium |
| **Web Dashboard** | Real-time charts, portfolio tracker, risk heatmap in browser | Medium |
| **New Analysis Strategies** | Custom indicator combinations, ML-based pattern detection, orderbook imbalance | Easy-Hard |
| **Multi-Language Telegram** | i18n support for bot messages (EN/ZH/ES/RU/AR) | Easy |
| **On-Chain Data Feeds** | Integrate whale wallet tracking, DEX flows, funding rates from on-chain sources | Medium |
| **Sentiment Feeds** | Twitter/X sentiment, Fear & Greed index, news NLP scoring | Medium |
| **Portfolio Optimization** | Kelly criterion sizing, correlation-aware allocation, Markowitz frontier | Hard |
| **Alerting System** | Push notifications for regime changes, anomaly detection, circuit breaker events | Easy |
| **Backtesting UI** | Visual backtest results with equity curves, trade markers, drawdown charts | Medium |
| **Multi-Agent Orchestration** | Expand swarm protocol -- specialist agents for different market regimes | Hard |

We believe the best hackathon projects are built on strong foundations. RUNECLAW provides the risk engine and market intelligence -- you bring the alpha.

```bash
# Get started in 60 seconds
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW && cp .env.example .env
pip install -r bot/requirements.txt
python -m bot.main --mode scan
```

> **Want to co-submit?** Open an issue titled "Co-submission: [Your Project Name]" and we'll coordinate.

---

## License

**AGPL-3.0** -- GNU Affero General Public License v3.0. See [LICENSE](./LICENSE) and [NOTICE](./NOTICE) for details.

You are free to view, study, fork, and modify this code. If you distribute it or run a modified version as a network service (SaaS, API, web app), you must release your source code under the same license. Commercial licensing inquiries: contact Humanoid Traders via the [Telegram community](https://t.me/+VRNgsmkR5pszZTdk).

---

<p align="center"><b>RUNECLAW</b> -- Where Viking grit meets algorithmic precision.</p>
<p align="center"><i>Forged for Bitget AI Base Camp · Hackathon S1 | System Prompt v2026</i></p>
