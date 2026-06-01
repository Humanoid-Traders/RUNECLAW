# Hackathon Submission

## Project Name

**RUNECLAW -- AI Trading Command Core**

## Team

Humanoid Traders (Patrick Baur -- Lead Developer | Daan -- Co-Founder & Strategy)

## Track

- **Primary:** Track 1 -- Trading Agent
- **Secondary:** Track 2 -- Trading Infra

---

## One-Line Tagline

Simulation-first AI trading agent with 18 pre-trade risk checks (17 fail-closed + 1 fail-open liquidity guard), regime-aware analysis, and human confirmation on every trade.

---

## Project Summary (under 200 words)

RUNECLAW is a modular AI trading assistant built for the Bitget ecosystem. It scans markets for volume anomalies and momentum shifts, generates explainable trade ideas using a 10-voter confluence scoring model (candlestick pattern detection, Fibonacci retracement zones, OBV trend, and 6 classic indicators) blended with LLM reasoning, and enforces 20 independent pre-trade risk checks (17 fail-closed + 1 fail-open liquidity guard) -- if any single check cannot be evaluated, the trade is rejected.

The agent operates as a 9-state finite state machine with complete audit logging of every state transition, risk decision, and trade outcome. Every trade requires human confirmation via Telegram before execution. Paper trading is the default mode; live trading requires two explicit environment flags.

Key capabilities: ADX-14 regime detection (trend/range/chop) with adaptive strategy parameters, trailing stops activated at 1R profit, per-symbol and portfolio-level exposure limits, circuit breaker with cooldown enforcement, and a backtesting engine with intrabar SL/TP simulation and realistic commission/slippage modeling.

Validated across 500 backtest runs (synthetic data — 5 market regimes, 20 symbols, 5 seeds), producing 889 trades (485 valid, 15 errors) with worst-case drawdown of 3.87%, best run +8.06%, avg return -0.46%, and zero crashed runs. 339 unit tests cover risk engine, portfolio, analyzer, backtest, smart money, multi-timeframe, strategy modes, explainability, learning system, token optimizer, FSM, integration scenarios, red team adversarial stress testing, black swan anomaly detection, sentiment fusion, and multi-agent swarm protocol.

---

## Feature Bullets

- **18-Check Risk Gate (17 Fail-Closed + 1 Fail-Open Liquidity Guard)** -- position size, daily loss, drawdown, max positions, R:R minimum, confidence threshold, correlation blocking, loss streak, entry price sanity, stop-loss required, stale data guard, cooldown timer, portfolio exposure, per-symbol exposure, volatility guard, circuit breaker, liquidity guard (fail-open), macro event guard
- **10-Voter Confluence Scoring** -- RSI-14, MACD (12/26/9), Bollinger Bands (20/2), Volume Spike, ADX-14, VWAP, OBV trend, candlestick pattern signal, Fibonacci zone, weighted and blended with LLM confidence (60/40 split)
- **Candlestick Pattern Detection** -- 14 patterns: doji, hammer, shooting star, spinning top, marubozu, bullish/bearish engulfing, bullish/bearish harami, tweezer top/bottom, morning star, evening star, three white soldiers, three black crows
- **Fibonacci Retracement Levels** -- swing high/low detection over 50-bar lookback, standard levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) with zone classification
- **Regime-Aware Analysis** -- ADX-14 classifies market as TREND_UP, TREND_DOWN, RANGE, or CHOP; strategy adapts SL/TP multipliers and applies confidence penalties accordingly
- **Adaptive ATR Risk Management** -- stop-loss and take-profit levels scale with volatility regime (high vol: 3.0/4.5x ATR, normal: 2.5/3.5x, low: 2.0/3.0x)
- **Trailing Stops** -- activated at 1R profit, trail at 1.5x ATR behind best price; responsible for 48.7% of all exits in backtesting (synthetic data) with net-positive aggregate PnL
- **9-State FSM** -- IDLE, SCANNING, ANALYZING, RISK_CHECK, CONFIRMING, EXECUTING, MONITORING, COOLING_DOWN, HALTED with validated transitions
- **Human-in-the-Loop** -- every trade requires Telegram confirmation with inline approve/reject keyboard
- **Simulation-First** -- paper trading by default ($10K virtual balance), live trading requires dual safety flag opt-in
- **Full Audit Trail** -- structured JSON logging of every decision, rejection, and execution with timestamps
- **626+ Unit Tests** -- risk engine, portfolio, analyzer, backtest, learning system (8 modules), token optimizer (4 layers), smart money engine, multi-timeframe analysis, strategy modes, explainability engine, FSM, integration, edge cases, audit fix validation, Qwen integration, Solana ecosystem, red team, black swan, sentiment, swarm, security suite (626 total)
- **AI Learning System** -- 8 integrated modules: experience memory, reflection engine, strategy evaluator (S/A/B/C/D tiers), pattern learner, macro learner (FOMC/CPI/NFP/PCE tracking), model comparer, prompt optimizer, feedback collector; all governed by immutable safety policy with blocked-action lists
- **LLM Token Optimizer** -- 4-layer cost reduction: semantic cache (TTL-bucketed), tiered pipeline (rules/mini/full), smart batching (5 symbols/call), adaptive frequency (skip LLM in quiet markets); up to 70% token savings
- **Smart Money Engine** -- liquidation cascade detection (funding + OI + CVD divergence), funding rate squeeze (contrarian positioning), whale flow tracking (rolling buy/sell with stealth accumulation detection), composite scoring normalized [-1,1]
- **Multi-Timeframe Analysis** -- HTF alignment (EMA20/50 across 1H/4H/1D), market structure (HH/HL/LH/LL), Break of Structure (BOS), Change of Character (CHoCH), swing detection
- **Adaptive Strategy Modes** -- 5 modes (Trend Continuation, Breakout, Mean Reversion, Liquidity Sweep, Conservative) with per-mode SL/TP multipliers, confidence requirements, and confluence boosts
- **Explainability Engine** -- structured reasoning chains, factor attribution with contribution percentages, compliance scoring (explainability + data sufficiency + risk documentation), MiCA-aligned audit trail
- **Macro Event Calendar** -- hardcoded 2026 FOMC/CPI/NFP/PCE schedule with 5-state risk machine (NORMAL, PRE_EVENT_CAUTION, EVENT_LOCKDOWN, POST_EVENT_VOLATILITY, BLACKOUT)
- **Multi-Provider LLM (Qwen-Ready)** -- any OpenAI-compatible provider via LLM_BASE_URL: Alibaba Qwen (DashScope), OpenRouter, Together AI, Fireworks, local vLLM/Ollama; drop-in Qwen integration for Hackathon S1 partner alignment
- **Solana Ecosystem Mode** -- ASSET_UNIVERSE=solana prioritizes 15 Solana tokens (SOL, JUP, JTO, BONK, WIF, PYTH, RAY, ORCA, RENDER, HNT, MOBILE, W, JITO, TENSOR, DRIFT) in scanner output; all tradeable on Bitget with full risk engine coverage
- **Red Team Stress Tester** -- 28 adversarial scenarios across 10 attack categories (flash crash, liquidity drain, correlated selloff, stale data, confidence manipulation, R:R gaming, circuit breaker evasion, zero/negative values, direction inversion, position flood); 100% pass rate verified
- **Black Swan Detector** -- statistical anomaly detection pre-empting circuit breaker; 5 anomaly types (correlation breakdown, volume collapse, price acceleration, volatility explosion, spread widening); triggers halt before 5% daily loss threshold
- **Sentiment Fusion Engine** -- 11th confluence voter; fear/greed index from momentum (40%), volume (30%), volatility (30%); contrarian logic at crowd extremes; funding-rate contrarian signals
- **Multi-Agent Swarm Protocol** -- 5 specialized agents (Scanner, Analyst, Risk, Executor, Sentinel) communicating via SwarmBus pub/sub; MCP-compatible architecture; Sentinel broadcasts HALT on severity >= 0.8

---

## Technical Overview

| Component | Implementation |
|---|---|
| Architecture | 9-state FSM governing full trade lifecycle from scan to cooldown, with HALTED state for circuit-breaker events |
| Market Scanner | Volume anomaly detection with 2x rolling average spike threshold, stale data eviction, thread-safe with RLock |
| Analysis Engine | 10-voter confluence model (RSI, MACD, BB, Volume Spike, ADX, VWAP, OBV trend, candlestick pattern, Fibonacci zone) + LLM reasoning + MTF alignment + smart money votes, SMA-50 trend alignment (+0.10/-0.15), volume confirmation (+/-0.05) |
| Regime Detection | ADX-14 with directional movement index; TREND_UP/DOWN skip opposite-direction signals, RANGE/CHOP apply confidence penalty |
| Smart Money | Liquidation cascade (funding + OI + CVD divergence), funding squeeze (contrarian), whale flow tracking, composite score (institutional 35% + contrarian 20% + whale 25% + cascade 20%) |
| Multi-Timeframe | EMA20/50 alignment across 1H/4H/1D, swing detection, market structure (HH/HL/LH/LL), BOS/CHoCH detection |
| Strategy Modes | 5 adaptive modes selected by regime + context: Trend Continuation, Breakout, Mean Reversion, Liquidity Sweep, Conservative |
| Explainability | Reasoning chains, factor attribution, compliance scoring (MiCA-aligned), natural language narratives |
| Risk Engine | 20 checks (17 fail-closed + 1 fail-open liquidity guard), all must pass; thread-safe with RLock; stats tracking for monitoring |
| Trailing Stops | Track best_price per position, activate at 1R profit, trail at 1.5x ATR; trailing exits lock in ≥1 ATR profit by construction (structural, not a predictive edge) |
| Circuit Breaker | Trips on 5% daily loss, 10% drawdown, or 5 consecutive losses; requires manual reset |
| Portfolio Tracker | Thread-safe position lifecycle with drawdown tracking, daily PnL, equity snapshots |
| Backtesting | Intrabar SL/TP/trailing stop checking, configurable commission (0.1%) and slippage (0.05%), synthetic data with GBM + GARCH |
| Telegram Bot | Rate-limited (20/min), inline keyboards, fire-and-forget async tasks with error callbacks |
| Data Validation | Pydantic strict schemas at every boundary -- API responses, config, trade parameters, internal state |
| AI Learning | 10-step workflow (Observe→Decide→Log→Simulate→Review→Score→Learn→Validate→Approve→Version); safety policy with blocked-action lists; may_override_risk enforced False via Pydantic validator |
| Token Optimizer | Semantic cache (regime+RSI+MACD keyed), tiered pipeline (3 tiers), smart batching (up to 5 symbols), adaptive frequency (ADX/volume gating) |
| Macro Calendar | 2026 FOMC/CPI/NFP/PCE schedule, 5-state risk machine with fail-closed BLACKOUT default |
| Concurrency | RLock guards on shared mutable state (portfolio, risk engine, scanner); single-threaded asyncio means contention is minimal, but locks protect against any future threading |
| LLM Providers | Multi-provider via LLM_BASE_URL: OpenAI (default), Alibaba Qwen (DashScope), OpenRouter, Together AI, Fireworks, local vLLM/Ollama. Drop-in swap, zero code changes |
| Solana Ecosystem | 15 Solana tokens tracked (SOL, JUP, JTO, BONK, WIF, PYTH, RAY, ORCA, RENDER, HNT, MOBILE, W, JITO, TENSOR, DRIFT); ASSET_UNIVERSE=solana prioritizes in scanner |
| Red Team Engine | 28 adversarial scenarios across 10 attack categories; attacks the real risk engine; 100% pass rate verified |
| Black Swan Detector | 5 statistical anomaly checks (correlation breakdown, volume collapse, price acceleration, ATR explosion, spread widening); pre-empts circuit breaker |
| Sentiment Engine | 11th confluence voter; fear/greed (0-100) from momentum/volume/volatility; contrarian logic at extremes; funding-rate signals |
| Multi-Agent Swarm | 5-agent swarm (Scanner/Analyst/Risk/Executor/Sentinel) with SwarmBus pub/sub; MCP-compatible; Sentinel HALT broadcasts |
| Metrics Engine | Sharpe/Sortino (per-trade returns, annualized from actual trade frequency), Calmar (return % / drawdown %), profit factor, equity curve (capped 10K points) |

---

## Links

| Resource | URL |
|----------|-----|
| **Live Bot** | **https://t.me/HTRUNECLAW_bot** |
| GitHub | https://github.com/Humanoid-Traders/RUNECLAW |
| Website | https://xbf5gmte.mule.page/ |
| GitBook | https://humanoid-traders-1.gitbook.io/humanoid-traders-ai |
| Telegram | https://t.me/+VRNgsmkR5pszZTdk |
| X / Twitter | https://x.com/BaurPatric70363 |

---

## Evidence Checklist

| Claim | Evidence | Status |
|-------|----------|--------|
| 18 risk checks | `bot/risk/risk_engine.py` lines 1-28 enumerate all 20 (16 in-engine + #17 liquidity + #18 macro) | Verified |
| Fail-closed design | Any check failure or exception returns REJECTED | Verified |
| 97+ tests passing | `pytest tests/ -v` -- 626 green (97 original + audit/learning/optimizer/qwen/solana/red-team/black-swan/sentiment/swarm/security additions) | Verified |
| 9-state FSM | `bot/utils/models.py` AgentState enum, `bot/core/engine.py` transitions | Verified |
| Trailing stops work | Backtest (synthetic data): 416/889 exits via trailing stop, net-positive aggregate PnL. Note: trailing exits are structurally profitable (activate at +1R, trail 1.5 ATR) — this is by construction, not evidence of predictive edge | Verified |
| Regime detection | `bot/core/analyzer.py` _detect_regime + _score_confluence | Verified |
| Thread safety | RLock on portfolio, risk engine, scanner; no await inside locks, safe for asyncio model | Verified |
| Simulation-first | `config.py` simulation_mode=True, live_trading_enabled=False by default | Verified |
| Live Telegram bot | @HTRUNECLAW_bot -- public, interactive, with role-based access, AI chat, dashboard panes | Verified |
| Red team 100% pass | 28 adversarial scenarios across 10 attack categories, all blocked by risk engine | Verified |
| Human confirmation | Telegram inline keyboard required before execution | Verified |
| Backtest validation | 500 runs (synthetic data, 5 regimes, 20 symbols, 5 seeds), 0 crashes, worst DD 3.87%, best run +8.06%, avg return -0.46% | Verified |
| Audit logging | `bot/utils/logger.py` structured JSON with timestamps | Verified |
| No deprecated APIs | All datetime.utcnow() migrated to datetime.now(UTC) | Verified |

---

## Final QA Checklist

- [x] All 339 tests pass (`pytest tests/ -v`)
- [x] No critical or high-severity issues in codebase audit (all C1-C3, H1-H4 fixed)
- [x] All 18 risk checks verified correct with unit tests
- [x] Backtest runs without crashes across 180 configurations
- [x] No hardcoded API keys or secrets in codebase
- [x] Config loads from environment variables with safe defaults
- [x] Simulation mode is ON by default
- [x] Live trading requires two explicit flags
- [x] README accurately reflects current architecture (20 checks: 17 fail-closed + 1 fail-open liquidity guard, 10+ voters including OBV, candlestick patterns, Fibonacci retracement, order flow when available)
- [x] Website matches codebase claims (20 checks, 289+ tests, backtest stats)
- [x] GitHub repo is public and up to date
- [x] No deprecated datetime calls remaining
- [x] Thread safety verified on all shared state
- [x] Memory management: equity curve capped, volume history bounded, TTL on pending ideas
- [x] All Pydantic models use strict validation
- [x] Footer disclaimer clearly states "educational prototype, not for real funds"
- [x] README includes Security section (API key handling, LLM costs, .env practices)
- [x] Limitations and Maturity section documents known gaps honestly

---

## Extension Roadmap

RUNECLAW is built to be forked and extended. The risk engine and market intelligence layer are production-grade foundations for new strategies, exchanges, and interfaces.

| Extension | Description |
|-----------|-------------|
| Multi-Exchange Connectors | Add OKX, Bybit, Binance adapters -- same risk engine, more markets |
| Web Dashboard | Real-time charts, portfolio tracker, risk heatmap in browser |
| New Analysis Strategies | Custom indicator combinations, ML pattern detection, orderbook imbalance |
| Multi-Language Telegram | i18n support for bot messages (EN/ZH/ES/RU/AR) |
| On-Chain Data Feeds | Whale wallet tracking, DEX flows, on-chain funding rates |
| Sentiment Feeds | Twitter/X sentiment, Fear & Greed index, news NLP scoring |
| Portfolio Optimization | Kelly criterion sizing, correlation-aware allocation |
| Multi-Agent Orchestration | Expand swarm protocol -- specialist agents per market regime |
