# Hackathon Submission

## Project Name

**RUNECLAW -- AI Trading Command Core**

## Team

Humanoid Traders

## Track

- **Primary:** Track 1 -- Trading Agent
- **Secondary:** Track 2 -- Trading Infra

---

## One-Line Tagline

Simulation-first AI trading agent with 16 fail-closed risk checks, regime-aware analysis, and human confirmation on every trade.

---

## Project Summary (under 200 words)

RUNECLAW is a modular AI trading assistant built for the Bitget ecosystem. It scans markets for volume anomalies and momentum shifts, generates explainable trade ideas using a 6-indicator confluence scoring model blended with LLM reasoning, and enforces 16 independent pre-trade risk checks in a fail-closed architecture -- if any single check cannot be evaluated, the trade is rejected.

The agent operates as a 9-state finite state machine with complete audit logging of every state transition, risk decision, and trade outcome. Every trade requires human confirmation via Telegram before execution. Paper trading is the default mode; live trading requires two explicit environment flags.

Key capabilities: ADX-14 regime detection (trend/range/chop) with adaptive strategy parameters, trailing stops activated at 1R profit, per-symbol and portfolio-level exposure limits, circuit breaker with cooldown enforcement, and a backtesting engine with intrabar SL/TP simulation and realistic commission/slippage modeling.

Validated across 180 backtest runs (3 volatility regimes, 3 trend biases, 20 seeds), producing 855 trades with worst-case drawdown of 2.87% and zero crashed runs. 82 unit tests cover risk engine, portfolio, analyzer, backtest, and integration scenarios.

---

## Feature Bullets

- **16-Check Fail-Closed Risk Gate** -- position size, daily loss, drawdown, max positions, R:R minimum, confidence threshold, correlation blocking, loss streak, entry price sanity, stop-loss required, stale data guard, cooldown timer, portfolio exposure, per-symbol exposure, volatility guard, circuit breaker
- **6-Indicator Confluence Scoring** -- RSI-14, MACD (12/26/9), Bollinger Bands (20/2), OBV, ADX-14, VWAP weighted and blended with LLM confidence (60/40 split)
- **Regime-Aware Analysis** -- ADX-14 classifies market as TREND_UP, TREND_DOWN, RANGE, or CHOP; strategy adapts SL/TP multipliers and applies confidence penalties accordingly
- **Adaptive ATR Risk Management** -- stop-loss and take-profit levels scale with volatility regime (high vol: 3.0/4.5x ATR, normal: 2.5/3.5x, low: 2.0/3.0x)
- **Trailing Stops** -- activated at 1R profit, trail at 1.5x ATR behind best price; responsible for 48.7% of all exits in backtesting with net-positive aggregate PnL
- **9-State FSM** -- IDLE, SCANNING, ANALYZING, RISK_CHECK, CONFIRMING, EXECUTING, MONITORING, COOLING_DOWN, HALTED with validated transitions
- **Human-in-the-Loop** -- every trade requires Telegram confirmation with inline approve/reject keyboard
- **Simulation-First** -- paper trading by default ($10K virtual balance), live trading requires dual safety flag opt-in
- **Full Audit Trail** -- structured JSON logging of every decision, rejection, and execution with timestamps
- **82 Unit Tests** -- risk engine, portfolio, analyzer indicators, backtest replay, models, integration, edge cases

---

## Technical Overview

| Component | Implementation |
|---|---|
| Architecture | 9-state FSM governing full trade lifecycle from scan to cooldown, with HALTED state for circuit-breaker events |
| Market Scanner | Volume anomaly detection with 2x rolling average spike threshold, stale data eviction, thread-safe with RLock |
| Analysis Engine | 6-indicator confluence model + LLM reasoning, SMA-50 trend alignment (+0.10/-0.15), volume confirmation (+/-0.05) |
| Regime Detection | ADX-14 with directional movement index; TREND_UP/DOWN skip opposite-direction signals, RANGE/CHOP apply confidence penalty |
| Risk Engine | 16 fail-closed checks, all must pass; thread-safe with RLock; stats tracking for monitoring |
| Trailing Stops | Track best_price per position, activate at 1R profit, trail at 1.5x ATR; 100% win rate in backtesting |
| Circuit Breaker | Trips on 5% daily loss, 10% drawdown, or 5 consecutive losses; requires manual reset |
| Portfolio Tracker | Thread-safe position lifecycle with drawdown tracking, daily PnL, equity snapshots |
| Backtesting | Intrabar SL/TP/trailing stop checking, configurable commission (0.1%) and slippage (0.05%), synthetic data with GBM + GARCH |
| Telegram Bot | Rate-limited (20/min), inline keyboards, fire-and-forget async tasks with error callbacks |
| Data Validation | Pydantic strict schemas at every boundary -- API responses, config, trade parameters, internal state |
| Concurrency | RLock guards on portfolio, risk engine, scanner; no await points inside locked regions so single-threaded asyncio model is safe |
| Metrics Engine | Sharpe/Sortino (annualized sqrt(2190) for hourly), Calmar, profit factor, equity curve (capped 10K points) |

---

## Links

| Resource | URL |
|----------|-----|
| GitHub | https://github.com/Humanoid-Traders/RUNECLAW |
| Website | https://lgl3crf9.mule.page/ |
| GitBook | https://humanoid-traders-1.gitbook.io/humanoid-traders-ai |
| Telegram | https://t.me/+VRNgsmkR5pszZTdk |

---

## Evidence Checklist

| Claim | Evidence | Status |
|-------|----------|--------|
| 16 risk checks | `bot/risk/risk_engine.py` lines 1-23 enumerate all 16 | Verified |
| Fail-closed design | Any check failure or exception returns REJECTED | Verified |
| 82 tests passing | `pytest tests/test_core.py -v` -- 82/82 green | Verified |
| 9-state FSM | `bot/utils/models.py` AgentState enum, `bot/core/engine.py` transitions | Verified |
| Trailing stops work | Backtest: 416/855 exits via trailing stop, net-positive aggregate PnL | Verified |
| Regime detection | `bot/core/analyzer.py` _detect_regime + _score_confluence | Verified |
| Thread safety | RLock on portfolio, risk engine, scanner; no await inside locks, safe for asyncio model | Verified |
| Simulation-first | `config.py` simulation_mode=True, live_trading_enabled=False by default | Verified |
| Human confirmation | Telegram inline keyboard required before execution | Verified |
| Backtest validation | 180 runs, 0 crashes, worst DD 2.87%, worst PnL -2.01% | Verified |
| Audit logging | `bot/utils/logger.py` structured JSON with timestamps | Verified |
| No deprecated APIs | All datetime.utcnow() migrated to datetime.now(UTC) | Verified |

---

## Final QA Checklist

- [x] All 82 tests pass (`pytest tests/test_core.py -v`)
- [x] No critical or high-severity issues in codebase audit
- [x] All 16 risk checks verified correct with unit tests
- [x] Backtest runs without crashes across 180 configurations
- [x] No hardcoded API keys or secrets in codebase
- [x] Config loads from environment variables with safe defaults
- [x] Simulation mode is ON by default
- [x] Live trading requires two explicit flags
- [x] README accurately reflects current architecture (16 checks, 6 indicators)
- [x] Website matches codebase claims (16 checks, 82 tests, backtest stats)
- [x] GitHub repo is public and up to date
- [x] No deprecated datetime calls remaining
- [x] Thread safety verified on all shared state
- [x] Memory management: equity curve capped, volume history bounded, TTL on pending ideas
- [x] All Pydantic models use strict validation
- [x] Footer links point to correct GitHub repo URL
