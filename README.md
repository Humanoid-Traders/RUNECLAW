```
 ____  _   _ _   _ _____ ____ _        ___        __
|  _ \| | | | \ | | ____/ ___| |      / \ \      / /
| |_) | | | |  \| |  _|| |   | |     / _ \ \ /\ / /
|  _ <| |_| | |\  | |__| |___| |___ / ___ \ V  V /
|_| \_\\___/|_| \_|_____\____|_____/_/   \_\_/\_/
```

<h3 align="center">AI Trading Command Core | Governed by Discipline.</h3>
<h4 align="center">by Humanoid Traders | for Bitget AI Base Camp</h4>
<h5 align="center">Built for Bitget AI Base Camp · Hackathon S1 — Strategy & Risk category</h5>

<p align="center">
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai"><img src="https://img.shields.io/badge/Full_Documentation-%E2%86%92_GitBook-blue?style=for-the-badge&logo=gitbook&logoColor=white" alt="Full Documentation → GitBook"></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-BUSL--1.1-blue" alt="License BUSL-1.1">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW/actions/workflows/ci.yml"><img src="https://github.com/Humanoid-Traders/RUNECLAW/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/tests-2644%20test%20functions%20%7C%20227%20files-brightgreen" alt="2644 Test Functions | 227 Files">
  <img src="https://img.shields.io/badge/security%20tests-29%20passing-blueviolet" alt="29 Security Tests">
  <img src="https://img.shields.io/badge/red%20team-28%20scenarios%20%7C%20framework%20included-critical" alt="Red Team 28 Scenarios | Framework Included">
  <img src="https://img.shields.io/badge/risk%20checks-23%20(16%20strict%20%2B%207%20advisory)-red" alt="23 Risk Checks">
  <img src="https://img.shields.io/badge/mode-live%20trading-green" alt="Live Trading">
  <img src="https://img.shields.io/badge/exchange-Bitget-blue" alt="Bitget">
  <img src="https://img.shields.io/badge/bot-LIVE%20%40HTRUNECLAW__bot-26a5e4?logo=telegram" alt="Live Telegram Bot">
  <img src="https://img.shields.io/badge/hackathon-AI%20Base%20Camp%20S1-purple" alt="AI Base Camp Hackathon S1">
</p>

<p align="center">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW">GitHub</a> &middot;
  <a href="https://pmvc58g2.mule.page/">Website</a> &middot;
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai">Documentation</a> &middot;
  <a href="https://t.me/HTRUNECLAW_bot">Live Bot</a> &middot;
  <a href="https://t.me/+VRNgsmkR5pszZTdk">Telegram</a> &middot;
  <a href="https://x.com/BaurPatric70363">X / Twitter</a>
</p>

<p align="center">
  <b>Try it live right now &rarr; <a href="https://t.me/HTRUNECLAW_bot">@HTRUNECLAW_bot</a></b>
</p>

<p align="center">
  <b>English</b> &middot; <a href="./README.zh-TW.md">繁體中文</a>
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

The system operates in **simulation-first mode by default**. Every trade idea must pass 23 pre-trade risk checks (16 strict fail-closed gates + 1 fail-open liquidity guard + 6 advisory checks that skip when data is unavailable), an adversarial self-critique gate, and receive explicit human confirmation before execution.

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

### Multi-Timeframe Scan Modes (NEW)
Three dedicated scan commands with rich dashboard-grade output:
- **`/scalp`** -- 5-minute candles, top 3 by volume, tight SL/TP for quick trades
- **`/intraday`** -- 15-minute candles, top 5 movers by price change
- **`/swing`** -- 4-hour candles, wide SL/TP, trend-following setups

Each scan produces 4 sections: **Account Status** (equity, positions, circuit breaker), **Live Tickers** (price, 24h change, volume table), **Regime Assessment** (per-asset narrative with RSI, VWAP, EMA20, support/resistance levels), and **Scan Verdict** (actionable trade ideas with entry/SL/TP/R:R and confidence bars).

### Multi-Asset Universe — Crypto + TradFi Perps (NEW)
Beyond crypto, RUNECLAW scans and trades **non-crypto USDT-M perpetuals on Bitget** — the same risk engine, AI analysis, and human-confirmation flow apply to every class. Switch focus instantly with `/mode <universe>` (or `ASSET_UNIVERSE` in `.env`), no restart required:

| `/mode` | Universe | Instruments |
|---------|----------|-------------|
| `all_markets` | Everything | crypto + all TradFi perps below |
| `solana` | Solana ecosystem | 15 SOL-ecosystem tokens (see below) |
| `metals` | Precious + industrial metals | Gold (XAU), Silver (XAG), Platinum (XPT), Palladium (XPD), Copper, PAX Gold |
| `commodities` | Energy | WTI Crude (CL), Brent (BZ), Natural Gas |
| `stocks` | US equity perps | TSLA, AAPL, MSFT, GOOGL, AMZN, META, NVDA, AMD, COIN, MSTR, HOOD, PLTR, ARM, MRVL, INTC |
| `etfs` | Sector / region ETFs | XLK, DFEN, KWEB, SGOV, EWH, INDA |
| `pre_ipo` | Pre-IPO tokens | OpenAI, Anthropic |
| `tradfi` | All non-crypto | metals + commodities + stocks + ETFs + pre-IPO |
| `hybrid` | Mixed | crypto majors + selected TradFi |

**TradFi-aware risk:** metals and energy trade 24/7; US stock/ETF perps respect **DST-aware US market hours** (the engine distinguishes regular vs extended session). Stock positions use tighter SL/TP multipliers and a max-correlated-stock cap (it won't stack 5 tech names at once), and metals carry sector tags (Precious / Industrial) for correlation-aware concentration limits.

### Solana Ecosystem Mode (NEW)
Set `ASSET_UNIVERSE=solana` in `.env` or use `/mode solana` to prioritize 15 Solana ecosystem tokens. All trade on Bitget with full USDT pair support.

**Tokens:** SOL, JUP, JTO, BONK, WIF, PYTH, RAY, ORCA, RENDER, HNT, MOBILE, W, JITO, TENSOR, DRIFT

**Solana-specific risk tweaks:**
- **Meme-coin volatility guard**: BONK and WIF use a tighter 4% ATR threshold (vs 6% default) to prevent entries during extreme volatility spikes
- **Ecosystem correlation group**: Non-meme Solana tokens (JUP, JTO, PYTH, RAY, etc.) are grouped as `SOLANA_ECO` -- the risk engine limits concentrated bets across correlated assets
- **Live mode switching**: `/mode solana` and `/mode all` switch scanner focus without restart

### Advanced Elliott Wave Analysis (NEW)
Multi-degree Elliott Wave with ZigZag detection, wave-position awareness, Fibonacci targets, and MTF alignment. Enabled by default (#236, #237). Features:
- **Multi-degree waves** -- detects impulse (1-5) and corrective (A-B-C) patterns at multiple degrees simultaneously
- **Wave-position aware** -- identifies current wave position for entry timing (e.g., Wave 3 extensions, Wave 5 termination)
- **Fibonacci targets** -- auto-computed extension targets (1.618, 2.618) for active impulse waves
- **ZigZag filtering** -- noise-robust swing detection via configurable threshold

### Advanced VWAP Analysis (NEW)
Session-anchored VWAP with band-reversion and slope-aware voting (#238):
- **Band reversion** -- mean-reversion signals from VWAP ±1σ/±2σ bands
- **Slope-aware votes** -- VWAP slope direction strengthens/weakens directional thesis
- **Anchored VWAP (AVWAP)** -- setup-anchored VWAP from significant swing points
- **Session anchoring** -- VWAP resets at session boundaries for intraday accuracy

### Portfolio Backtester (NEW)
Multi-symbol shared-risk backtests with realistic portfolio constraints (#248):
- **Shared capital** -- multiple symbols compete for the same equity pool
- **Cross-position risk** -- portfolio-level drawdown and exposure limits applied during backtest
- **Breaker reset bars** -- configurable circuit breaker auto-reset prevents long backtests from halting permanently (#245)

### Level-Aware SL/TP (NEW)
Stop-loss and take-profit placement that respects market structure (#251):
- **Support/resistance awareness** -- SL placed beyond the nearest support/resistance level, not at a fixed ATR multiple
- **Swing-level snapping** -- TP snaps to nearest Fibonacci extension or swing target
- **Detector math fixes** -- corrected calculation errors in anomaly detector scoring (#251)

### Ops Hardening (NEW)
Production operations improvements (#240, #241, #246):
- **Reconcile-close lock** -- prevents double-close race conditions during position reconciliation
- **Dead-man's-switch ping** -- periodic health pings to detect silent failures
- **Deep-history Bitget fetch** -- paginate past the per-call candle cap for longer backtests (#244)
- **Proxy env support** -- honors HTTP_PROXY/HTTPS_PROXY in all ccxt Bitget clients (#242)
- **Live hardening defaults** -- stage-1/2 hardening flags enabled by default (#241)
- **External sentiment default** -- Fear & Greed index enabled by default (#247)

### Learning Readiness Automation (NEW)
Automated learning module readiness tracking with chart regime ribbon and trade annotations (#255):
- **Readiness dashboard** -- shows which learners have enough data to activate
- **Chart regime ribbon** -- visual regime overlay on equity curve
- **Trade annotations** -- marks entry/exit points with win/loss coloring on charts
- **OI warm-start** -- open interest data pre-fetched at startup for immediate derivative analysis


Talk to RUNECLAW in plain English instead of memorizing commands:
- **Intent routing**: "how's Bitcoin?" dispatches to `/analyze BTC`, "what's moving?" triggers `/scan`
- **Symbol extraction**: understands tickers (`$ETH`), names (`Solana`), and pairs (`BTC/USDT`)
- **LLM fallback**: when rule-based patterns don't match, optional LLM classification routes to the right skill
- **AI chat**: unmatched messages get a contextual response from the LLM (never invents trades)

### Bilingual Interface — English / 繁體中文 (NEW)
The entire Telegram interface is **localized in English and Traditional Chinese (繁體中文)**:
- **Per-user language preference** -- each user picks their language with `/lang`; the choice is stored per user, so messages, menus, alerts, and risk explanations all render in the selected language.
- **Full coverage** -- commands, confirmations, risk-check verdicts, trade notifications, and error messages are all translated (see `bot/utils/i18n.py`, `SUPPORTED_LANGS = {en, zh}`).
- **Safe fallback** -- any untranslated key falls back to English rather than failing.

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
Red-team testing framework included -- runs 28 adversarial scenarios to verify risk gate behavior. Includes ATR=0 bad-data test.

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

### Sentiment Proxy Engine (Price/Volume-Based) (NEW)
Price-derived sentiment proxy as the 11th confluence voter. Combines:
- **Fear & Greed Index** (0-100) from price momentum (40%), volume trend (30%), volatility (30%)
- **Contrarian logic**: extreme fear -> bullish vote [+0.3, +0.6], extreme greed -> bearish vote [-0.6, -0.3]
- **Funding-rate contrarian**: extreme positive funding adds bearish offset, extreme negative adds bullish

### Multi-Agent Swarm Protocol (NEW)
Composable agent collaboration via experimental, in-process pub/sub architecture. Five specialized agents:
Scanner (perceives market), Analyst (generates theses), Risk (gates every trade), Executor (manages positions),
Sentinel (monitors for black swans). Communication via SwarmBus pub/sub, with Sentinel broadcasting HALT
to all agents when severity >= 0.8. Ready for production deployment as separate Agent Hub agents.

### Multi-User Live Trading (NEW)
RUNECLAW supports **multiple users trading live on their own Bitget accounts**, each isolated from the others. Default OFF — set `PER_USER_LIVE_ENABLED=true` to enable. Every layer is gated and the operator path is byte-identical until enabled:
- **Own-account execution** -- each user links their own keys via `/connect` (Fernet-encrypted at rest); their confirmed trades execute on **their** account, never the operator's. A user with no linked keys is rejected, never silently routed.
- **Per-user risk isolation** -- each user gets their own circuit breaker, loss streak, daily-loss, and drawdown state; one user's halt never stops anyone else.
- **Own-equity sizing** -- positions are sized against the user's own balance, not the operator's.
- **Per-user margin caps** -- admins cap a user's per-trade margin with `/setcap` (tighten-only, never above the global live cap).
- **Global kill-switch** -- the emergency stop / `/closeall` flattens **every** account (operator + all per-user) and halts all risk engines at once; `/reset` resumes.
- **Per-user observability** -- `/accounts` shows each account's live equity, open positions, exposure, breaker state, governor throttling, and configured caps.
- **Dedicated access allowlist** -- onboard regular live users via `LIVE_TRADER_TELEGRAM_IDS` (grants live-trade permission **without** operator/admin privileges), then `/approve` + `/grant_live`.

> Live enablement, hardening order, and a first-live-user pre-flight checklist are documented in `docs/MULTI_USER_LIVE_SETUP.md`, `docs/LIVE_HARDENING_RUNBOOK.md`, and `docs/FIRST_LIVE_USER_PREFLIGHT.md`.

### Realized-Performance Governor (NEW)
A closed-loop backstop on top of the pre-trade checks (gated `LIVE_PERFORMANCE_GOVERNOR_ENABLED`). It scores the realized win rate and net PnL of recent **closed** trades per account and automatically **reduces** position size when results degrade — or **pauses** trading when an account is both losing often and net-negative. Tighten-only; surfaced in `/accounts`.

---

## Architecture

```
 Telegram Bot        API Bridge (8000)      Bitget Exchange
      |                    |                      |
      v                    v                      v
 +-----------+    +---------------+   +-----------+
 |  Skill    |--->|  RuneClaw     |-->|  Market   |
 |  Registry |    |  Engine       |   |  Scanner  |
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

**Runtime services:**
- **Telegram Bot** (port 8080 internal) -- command interface, human-in-the-loop confirmation
- **API Bridge** (port 8000) -- FastAPI REST API exposing engine endpoints (`/health`, `/scan`, `/portfolio`, `/risk/status`, `/confirm`)
- **Redis** (port 6379 internal, not host-exposed) -- LLM cache, rate limiting, session state
- **Dashboard** (served via API Bridge) -- War Room, Live Signals, portfolio views

**Pipeline:** SCAN --> ANALYZE --> RISK GATE --> HUMAN CONFIRM --> EXECUTE (paper or live; live is gated and human-confirmed, with admin-only auto-execution)

### Runtime Services

| Service | Port | Description |
|---------|------|-------------|
| **bot** | 8080 (internal) | Telegram bot + dashboard server. Socket healthcheck on 8080. |
| **api_bridge** | 8000 | FastAPI REST API for external integrations (War Room, live signals, MCP). Healthcheck on `/health`. |
| **redis** | 6379 (internal) | Session state, LLM cache, rate limiting. AOF persistence. Password-protected, not host-exposed. |
| **nginx** | 80/443 | TLS reverse proxy (optional). Serves static website, proxies `/api/*` to api_bridge. |

> **Note:** The bot service runs an HTTP server on port 8080 for the dashboard even in `--mode telegram`. The healthcheck depends on this. Redis is internal to the Docker network only.

---

## Features

### Market Intelligence
- Real-time scanning of 67 Bitget USDT pairs (API bridge) / 324+ pairs (Telegram bot)
- **Multi-asset:** crypto plus non-crypto USDT-M perps — metals (gold/silver/platinum/palladium/copper), energy (WTI/Brent/NatGas), US stock perps (TSLA, NVDA, AAPL, …), ETFs, and pre-IPO tokens — switchable via `/mode` (see Multi-Asset Universe)
- Volume spike detection (2x rolling average)
- Momentum scoring with configurable thresholds
- Top N mover ranking with structured signal output

### AI Analysis Engine
- Technical indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR-14, ADX-14, VWAP, SMA-50 trend alignment, On-Balance Volume (OBV), Rolling VWAP (20-bar and 50-bar)
- Candlestick pattern detection: 14 patterns (detected, contributed to pattern scoring) including doji, hammer, shooting star, engulfing, harami, tweezer top/bottom, morning/evening star, three white soldiers, three black crows
- Fibonacci retracement levels: swing high/low detection over 50-bar lookback, standard levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) with zone classification
- 30+ voter confluence scoring model (expanded from the original 6; some voters derived from overlapping data sources): momentum (RSI, MACD, Stochastic), trend (ADX, EMA ribbon, VWAP), volume (spike, OBV, volume profile/POC), structure (Bollinger %B, Donchian, Keltner squeeze, Fibonacci), pattern (chart patterns, Elliott, Wyckoff, harmonics, liquidity sweep), order-flow (CVD, book imbalance, whale prints, funding), plus multi-timeframe, smart-money, and sentiment voters and the blended LLM confidence. Optional family-cap de-correlation and learned per-voter weights.
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
- **21-check risk gate** -- 16 strict fail-closed (one failure = rejection), 1 fail-open (#17 LIQUIDITY: no order-book data = pass), 4 advisory/skip (#18 MACRO, #19 MTF, #20 PCA, #21 VaR: skip when data unavailable), plus 2 additional advisory gates (#22 Taker 3-bar, #23 Bid dominance). See `config/risk_manifest.yaml` for the authoritative list.
- Circuit breaker halts trading on daily loss or drawdown breach
- Fixed-fractional position sizing: risk budget (2% of equity) divided by stop distance, capped at 20% notional
- Max open positions limit
- Risk/reward ratio minimum (1.2x)
- Confidence threshold gate (≥60%)
- Per-symbol exposure limit (20% max per asset)
- Correlation group concentration guard (max 2 positions per group, e.g., ALT_L1, SOLANA_ECO)
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

### Backtesting

RUNECLAW provides two backtest modes:

| Mode | Script | Data Source | LLM | Purpose |
|------|--------|-------------|-----|---------|
| **Synthetic** | `backtest_audit.py` | GBM+GARCH random walks | Off | Risk gate sanity checks on noise |
| **Real-data** | `backtest_realdata.py` | Bitget historical OHLCV | Configurable | Strategy performance validation |

```bash
# Synthetic backtest (validates risk engine behavior)
python backtest_audit.py

# Real-data backtest with buy-and-hold benchmark
python backtest_realdata.py --symbols default

# Real-data with LLM analysis enabled
python backtest_realdata.py --symbols all --llm
```

**Methodology transparency:**
- Synthetic backtests use random-walk data and **cannot** validate alpha-generating modules (Smart Money, order flow, sentiment, liquidation cascade). They demonstrate the risk gate and rule-based fallback behave correctly under various noise regimes.
- Real-data backtests use actual Bitget OHLCV with commission (0.10%) and slippage (0.05%) modeling, and include a buy-and-hold benchmark for comparison.
- Results with `--llm` flag reflect the full AI analysis pipeline; without it, only the rule-based fallback runs.

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
| `/start` | Main menu with War Room navigation |
| `/status` | Engine status, health score, capital, risk gauge |
| `/scan` | Scan market for top movers and volume spikes |
| `/scalp` | Rich scalp scan (5m candles, top 3 by volume) |
| `/intraday` | Rich intraday scan (15m candles, top 5 movers) |
| `/swing` | Rich swing scan (4h candles, trend-based) |
| `/analyze BTC` | Run AI analysis on a specific asset |
| `/run` | Strategy presets (dip sniper, momentum, scalper) |
| `/portfolio` | View paper portfolio with PnL waterfall |
| `/trade` | View and confirm/reject pending trades |
| `/journal` | Trade history with win/loss breakdown |
| `/risk` | Risk dashboard with visual gauges |
| `/rejected` | Recent risk-rejected trades with failure reasons |
| `/whynot [SYM]` | Explain why a trade was rejected |
| `/dashboard` | Command center (status/risk/positions tabs) |
| `/backtest` | Run backtest with synthetic data (rule-based only, LLM off) |
| `/walkforward` | Walk-forward validation (overfitting detection) |
| `/macro` | Macro event calendar (FOMC, CPI, NFP) |
| `/learn` | AI learning system dashboard (8 modules) |
| `/patterns` | View detected market patterns |
| `/proposals` | View pending improvement proposals |
| `/optimize` | LLM token optimization stats |
| `/costs` | Agent economics (LLM + infra breakdown) |
| `/watch on\|off` | Toggle proactive alerts |
| `/halt` | Emergency kill-switch (trip breaker on ALL accounts, cancel all) |
| `/closeall` | Admin: flatten open positions on every account (operator + per-user) |
| `/pause` / `/resume` | Pause/resume trading |
| `/mode <universe>` | Switch asset universe — `solana`, `metals`, `commodities`, `stocks`, `etfs`, `pre_ipo`, `tradfi`, `hybrid`, `all_markets` (no restart) |
| `/setllm` | Switch LLM provider at runtime (BYOK) |
| `/llmstatus` | Current LLM provider and model info |
| `/lang` | Switch interface language — English / 繁體中文 (per-user) |
| `/paper on\|off` | Toggle paper vs live execution for your trades |
| `/help` | List all available commands |

### Scanning, analysis & strategy commands

| Command | Description |
|---------|-------------|
| `/deepscan` / `/fullscan` | Multi-timeframe deep scan / full-universe scan |
| `/stockscan` | Scan the US stock-perp universe |
| `/forcescan` | Force an immediate scan cycle |
| `/momentum` `/dip` `/squeeze` `/sweep` `/zones` | Strategy preset shortcuts (aliases for `/run <preset>`) |
| `/buy <SYM>` / `/sell <SYM>` | Stage a manual long/short trade idea (still risk-gated + confirmed) |
| `/strategy` | Active strategy + regime routing |
| `/session` | Current trading-session sizing context |
| `/performance` | Performance summary (win rate, PnL, R) |
| `/daily_report` | End-of-day trading report (trades, wins/losses, best/worst, PnL) |
| `/equitycurve` | Equity-curve view |
| `/holdtime` | Hold-time distribution analytics |
| `/attribution` | Per-factor PnL attribution |
| `/crossasset` | Cross-asset correlation view |
| `/montecarlo` | Monte-Carlo robustness simulation |
| `/signals` / `/latest_signal` | Signal stats / most recent signal |
| `/orders` / `/open_positions` | Open orders / open positions |
| `/autoconfirm` | Toggle admin auto-confirm (0.85 gate) |
| `/playbook` | GetAgent playbook controls |
| `/llmtiers` / `/llmreset` | LLM tier routing config / reset |
| `/set_tier` `/revoke` | Admin: set a user's tier / revoke access |
| `/channel` `/broadcast` | Admin: marketing channel forwarder |

### Live & multi-user commands

| Command | Who | Description |
|---------|-----|-------------|
| `/connect <key> <secret> <pass>` | user | Link your own Bitget account (DM only; validated, encrypted at rest) |
| `/disconnect` | user | Remove your linked Bitget keys |
| `/exchange` | user | Check your linked-account status |
| `/livebalance` | user | Your live Bitget balance |
| `/livepositions` | user | Your open live positions with SL/TP |
| `/liveclose <id>` | user | Close one of your live positions |
| `/golive CONFIRM` | admin | Arm live trading (when not env-armed) |
| `/approve <id> [role]` | admin | Approve a pending user (trader/viewer/admin) |
| `/grant_live <id>` / `/revoke_live <id>` | admin | Grant/revoke a user's live-trade permission |
| `/setcap <id> <usd\|off>` | admin | Cap a user's per-trade margin (tighten-only) |
| `/accounts` | admin | Per-account live risk: equity, exposure, breaker, governor, caps |
| `/users` | admin | Registered-user roster (role, tier, mode) |
| `/health` | admin | Engine vitals (WS, balance, tick health) |
| `/slippage` | admin | Execution-quality / slippage drift |
| `/calibration` | admin | Confidence-calibration learner readiness |

> Multi-user live trading is OFF by default (`PER_USER_LIVE_ENABLED`). See `docs/MULTI_USER_LIVE_SETUP.md` for onboarding.

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
|   |   |-- analyzer.py         # AI + technical analysis, 30+ voter confluence
|   |   |-- order_flow.py       # Exchange microstructure: CVD, book imbalance, whales
|   |   |-- smart_money.py      # Liquidation cascade, funding squeeze, whale tracking
|   |   |-- multi_timeframe.py  # HTF alignment, market structure, BOS/CHoCH
|   |   |-- strategy_modes.py   # 5 adaptive strategy modes with per-mode configs
|   |   |-- red_team.py         # 28-scenario adversarial stress tester
|   |   |-- black_swan.py       # Statistical anomaly detection (5 anomaly types)
|   |   |-- sentiment.py        # Sentiment proxy engine (price/volume-based, 11th confluence voter)
|   |   |-- swarm.py            # Multi-agent swarm protocol (experimental, in-process pub/sub)
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
|   |-- test_core.py            # 383 core engine tests
|   |-- test_quant_skill.py     # 95 quant skill tests
|   |-- test_learning.py        # 77 learning system tests
|   |-- test_intent_and_monitor.py  # 47 intent routing + monitor tests
|   |-- test_learning_cannot_override_risk.py  # 45 safety policy tests
|   |-- test_ux_upgrades.py     # 39 UX upgrade tests
|   |-- test_token_optimizer.py # 36 token optimizer tests
|   |-- test_risk_upgrades.py   # 31 risk upgrade tests
|   |-- test_quant_upgrades.py  # 31 quant upgrade tests
|   |-- test_intelligence_upgrades.py  # 30 intelligence tests
|   |-- test_security.py        # 29 security tests
|   |-- test_macro.py           # 27 macro calendar tests
|   |-- test_var_critique_attestation.py  # 25 VaR/critique/attestation tests
|   |-- test_execution_upgrades.py  # 25 execution upgrade tests
|   |-- test_logic_bugs.py      # 24 logic regression tests
|   |-- test_exchange_and_compliance.py  # 20 exchange/compliance tests
|   |-- test_manifest_and_whynot.py  # 10 manifest tests
|   |-- test_live_executor.py   # 7 live executor tests
|   |-- test_telegram_commands.py  # Telegram command tests
|   |-- selftest_upgrade.py     # Self-test upgrade harness
|   |-- (2644 total test functions across 227 files)
|-- docs/
|   |-- gitbook/                # Full GitBook documentation
|   |-- SUBMISSION.md           # Hackathon submission document
|-- demo/
|   |-- sample_output.json      # Example trade idea
|   |-- sample_risk_check.json  # Example risk check
|   |-- sample_portfolio.json   # Example portfolio state
|-- website/
|   |-- index.html              # Gateway landing page (platform lives in app/)
|   |-- submission.html         # Hackathon submission (archive)
|-- .github/
|   |-- workflows/
|       |-- ci.yml                 # CI/CD: planned (not yet active)
|-- .env.example
|-- pyproject.toml
|-- Dockerfile
|-- backtest_audit.py              # Synthetic data sanity check
|-- run_deep_backtest.py           # 500-run robustness sweep
|-- run_realdata_backtest.py       # Real-data backtest with benchmarks
|-- LICENSE
|-- README.md
```

---

## Safety and Risk

RUNECLAW is designed with a **fail-closed** philosophy:

- **Simulation by default.** Live trading requires two explicit environment flags.
- **Every trade passes 23 checks.** 16 strict fail-closed, 1 fail-open (liquidity), 6 advisory/skip. See `config/risk_manifest.yaml` for details.
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
- **No secrets in code.** All credentials load from environment variables with safe defaults. Run `gitleaks` or `trufflehog` over full history to verify.

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
| BUSL | Compliance | `/start` and `/help` include source repository link and financial disclaimer |
| Corruption | Hardening | Portfolio logs CRITICAL alert on corrupted state files instead of silent fallback |

**29 dedicated security tests** in `tests/test_security.py` covering: log redaction, MCP auth, runtime state, cache keys, cost reset, portfolio corruption, input validation, and injection prevention. Note: security audit was AI-assisted and internal; no independent third-party audit has been performed.

---

## Live Trading Records

RUNECLAW is **live trading on Bitget futures** with micro-size positions. All trades are executed via the Telegram bot interface with human confirmation.

**Trading Period:** June 17-19, 2026  
**Exchange:** Bitget USDT-M Futures  
**Position Size:** $10-20 per trade (micro test mode)  
**Leverage:** 5x  
**Total Closed Trades:** 38  
**Win Rate:** 55.3% (21W / 17L)  
**Total Realized PnL:** +$46.30  

### Files in `logs/`

| File | Description |
|------|-------------|
| `live_trading_log.csv` | Complete trade log with timestamp, pair, side, entry/exit price, size, PnL |
| `closed_trades.json` | Raw closed trade records from the bot's state file |
| `audit_chain.jsonl` | Immutable audit chain -- every trade decision logged with context |

### Order Execution Features

- **POST_ONLY limit orders** -- guarantees maker-only execution, exchange rejects if would cross the book
- **Limit order price validation** -- conditional recalculation only when limit price would instant-fill
- **Price-drift cancellation** -- stale pending limits auto-cancelled when market moves >2% away
- **4-hour time expiry** -- unfilled limits cancelled after configurable timeout
- **Exchange-reported PnL** -- uses Bitget's actual `profit` field, not estimated calculations
- **Trade deduplication** -- prevents double-counting from reconciliation + manual close paths

---

## Limitations and Maturity

This is a **hackathon prototype** (maturity: early-stage). Known limitations:

- **Solo developer project** -- limited peer review beyond automated audits
- **Live trading active** -- RUNECLAW is live on Bitget futures with micro-size positions ($10-20 per trade, 5x leverage). See `logs/` for full trading records.
- **Backtest methodology caveat** -- backtests use synthetic GBM+GARCH price data with `use_llm=False`. This validates risk gate behavior and position sizing on random walks, but does **not** validate the alpha-generating modules (Smart Money, order flow, sentiment fusion, liquidation cascade) which require real market microstructure data. Backtest results should be interpreted as **engine sanity checks**, not evidence of profitability. Real-data, LLM-enabled, out-of-sample validation is needed to evaluate strategy performance.
- **API latency and slippage** -- real exchange conditions differ from simulation
- **Security audit conducted** -- AI-assisted deep audit (v3.0) with all 5 critical issues fixed, 29 security tests added. No independent third-party audit has been performed.
- **LLM dependency** -- AI analysis quality depends on model availability and cost
- **No guaranteed uptime** -- no monitoring, alerting, or failover infrastructure
- **Scalability:** Single-instance today -- swarm uses experimental in-process pub/sub (not a production MCP deployment)
- **Correlation guard** -- currently implemented as a per-group count cap (max 2 positions per correlation group), not a full pairwise correlation matrix. The `MAX_CORRELATION` config knob is reserved for future implementation.
- **Confluence voters** -- the expanded 30+ voter model includes many indicators derived from the same price-volume series (RSI, MACD, OBV, VWAP, Bollinger Bands), which are not statistically independent. Naive summation may double-count momentum signals. Weighted scoring -- plus an optional family-cap de-correlation pass (`CONFLUENCE_FAMILY_CAP_ENABLED`) and learned per-voter weights -- mitigates this but does not eliminate it.

### Backtest Methodology

Three backtest harnesses, each with a different purpose:

| Script | Data Source | LLM | Purpose |
|--------|-----------|-----|---------|
| `backtest_audit.py` | Synthetic (GBM+GARCH) | Off | Engine sanity check -- risk gate behavior on noise |
| `run_realdata_backtest.py` | **Real Binance OHLCV** | Configurable | Strategy validation with buy-and-hold benchmarks |
| `run_deep_backtest.py` | Synthetic (GBM+GARCH) | Off | 500-run robustness sweep (5 regimes x 20 symbols x 5 seeds) |

Synthetic backtests validate the **risk engine and rule-based fallback only** -- they do not exercise the AI or market microstructure modules. The real-data backtest uses walk-forward out-of-sample validation (70/30 split) and is the appropriate instrument for evaluating strategy edge.

```bash
# Real-data backtest (no API key needed):
python run_realdata_backtest.py

# With LLM enabled:
python run_realdata_backtest.py --llm --output results.json
```

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
| Pre-trade risk checks | **23 checks (16 strict + 7 advisory)** | 0-3 basic checks |
| Fail-closed design | **Yes** -- any failure = rejection | Fail-open (errors skip checks) |
| Circuit breaker | **Auto-halt** on daily loss / drawdown | None or manual only |
| Human confirmation | **Required** via Telegram keyboard | Auto-execute or no gate |
| Regime detection | **ADX-14 regime filter** blocks counter-trend | Not considered |
| Confluence scoring | **30+ voter model** (with optional family-cap de-correlation) | 1-2 indicators |
| Audit trail | **Full JSONL** -- every decision logged | Minimal or none |
| Simulation-first | **Default mode** -- live requires 2 explicit flags | Often live by default |
| Position sizing | **Fixed-fractional** with exposure caps | Fixed lot or % of balance |
| Re-check on confirm | **Yes** -- market may have moved | No re-validation |
| Backtest engine | **Built-in** with commission + slippage modeling | External or none |
| Live market connectivity | **324+ pairs scanned** on real Bitget data (read-only market data) | Mock data only |

> Safety and transparency are first-class design goals, not afterthoughts.

---

## Fork & Win With Us

RUNECLAW is open for collaboration. If you're building for the Bitget AI Base Camp and want a risk engine, scanner, or analysis pipeline -- fork it, extend it, and submit your own entry.

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
| **More Languages** | English + Traditional Chinese (繁體中文) ship today via `/lang`; add ES/RU/AR/etc. on the same i18n layer | Easy |
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

**BUSL-1.1** -- Business Source License 1.1 (source-available). See [LICENSE](./LICENSE) and [NOTICE](./NOTICE) for details.

You are free to view, study, fork, and modify this code, and to run it for your own personal or internal-business purposes (including trading your own or your organization's funds) and for research and evaluation. You may **not** offer RUNECLAW, or a derivative of it, to third parties as a hosted, managed, or embedded commercial service, or otherwise use it to compete with RUNECLAW or Humanoid Traders, without a commercial license. On the Change Date (2030-07-21), each version converts to the GNU General Public License, version 2.0 or later. Commercial licensing inquiries: contact Humanoid Traders via the [Telegram community](https://t.me/+VRNgsmkR5pszZTdk).

---

<p align="center"><b>RUNECLAW</b> -- Discipline over prediction. Transparency over hype.</p>
<p align="center"><i>Built for Bitget AI Base Camp · Hackathon S1</i></p>
