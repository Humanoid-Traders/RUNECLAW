# Hackathon Submission

## Project Name

**RUNECLAW -- AI Trading Command Core**

## Team

Humanoid Traders

## Track

- **Primary:** Track 1 -- Trading Agent
- **Secondary:** Track 2 -- Trading Infra

---

## Submission Text

RUNECLAW is a simulation-first AI trading agent built for the Bitget ecosystem. It autonomously scans markets for volume anomalies and momentum shifts, generates explainable trade ideas using LLM reasoning blended with a 6-indicator confluence scoring model, and enforces 15 independent pre-trade risk checks in a fail-closed architecture -- if any single check cannot be evaluated, the trade is rejected.

Every trade requires human confirmation via Telegram before execution. The agent operates as a formal 9-state finite state machine (IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING -> COOLING_DOWN -> HALTED), with complete audit logging of every state transition, risk decision, and trade outcome.

Key differentiators: regime-aware analysis (ADX-14 trend/range/chop detection), institutional-grade risk controls (circuit breaker, correlation blocking, portfolio exposure limits, cooldown timers, volatility guard), and a backtesting engine with intrabar stop-loss/take-profit checking, realistic commission and slippage modeling.

RUNECLAW ships paper-trading by default with dual safety flags. No real money is ever at risk without explicit opt-in. Built with Python, Bitget API, Telegram Bot API, and Pydantic strict schemas.

---

## Links

| Resource  | URL                                                              |
|-----------|------------------------------------------------------------------|
| GitHub    | https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER           |
| GitBook   | https://humanoid-traders-1.gitbook.io/humanoid-traders-ai         |
| Telegram  | https://t.me/+VRNgsmkR5pszZTdk                                   |

---

## Technical Summary

| Feature | Description |
|---|---|
| Architecture | 9-state finite state machine governing the full trade lifecycle from market scan to cooldown, with explicit HALTED state for circuit-breaker events. |
| Market Scanner | Autonomous detection of volume anomalies and momentum shifts across Bitget-listed pairs, filtered by liquidity and spread thresholds. |
| Analysis Engine | 6-indicator confluence scoring model (RSI, MACD, Bollinger Bands, OBV, ATR, ADX) combined with LLM-generated reasoning for explainable trade ideas. |
| Regime Detection | ADX-14 based market regime classification (trending, ranging, choppy) to adapt strategy parameters and filter low-conviction setups. |
| Risk Engine | 15 independent pre-trade risk checks executed in fail-closed mode. Covers position sizing, portfolio exposure, correlation blocking, drawdown limits, volatility guard, and cooldown enforcement. |
| Circuit Breaker | Automated system halt triggered by consecutive losses, max daily drawdown, or abnormal market conditions. Requires manual reset. |
| Human-in-the-Loop | Telegram-based trade confirmation flow. No order is submitted without explicit operator approval. |
| Audit Logging | Complete structured logs of every state transition, risk gate evaluation, trade decision, and execution outcome. |
| Backtesting Engine | Historical strategy validation with intrabar stop-loss and take-profit simulation, realistic commission modeling, and configurable slippage. |
| Paper Trading | Default execution mode using simulated fills. Live trading requires two independent safety flags to be toggled. |
| Data Validation | Pydantic strict schemas enforced at every system boundary -- API responses, configuration files, trade parameters, and internal state objects. |
| Telegram Bot | Operator interface for trade confirmations, system status queries, position summaries, and manual overrides. |
| Bitget Integration | Native Bitget API client for market data, order placement, and account management with automatic rate-limit handling. |
| Deployment | Containerized Python application with environment-based configuration and secret management. |
