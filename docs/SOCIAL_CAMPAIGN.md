# RUNECLAW Social Media Campaign

Target: Community Impact Award

---

## Twitter/X Thread

### Tweet 1 -- Launch Announcement

Introducing RUNECLAW -- an AI trading agent built for the Bitget ecosystem.

Track 1 (Trading Agent) | Track 2 (Trading Infra)

Key differentiator: simulation-first architecture with 16 independent risk checks in a fail-closed design. No trade executes without passing every gate.

Thread below.

### Tweet 2 -- Risk Engine

RUNECLAW enforces 16 pre-trade risk checks. Every single one must pass, or the trade is rejected. There is no override.

Circuit breaker. Correlation blocking. Portfolio exposure caps. Drawdown limits. Volatility guard. Cooldown timers.

This is not a suggestion engine. It is a control system.

### Tweet 3 -- Explainability and Transparency

Every decision RUNECLAW makes is logged and auditable.

State transitions, risk gate evaluations, indicator scores, LLM reasoning, execution outcomes -- all captured in structured audit logs.

If the system rejects a trade, you can trace exactly which check failed and why. No black boxes.

### Tweet 4 -- Simulation-First, Human-in-the-Loop

RUNECLAW ships in paper-trading mode by default. Two independent safety flags must be toggled before any real capital is at risk.

Every trade requires human confirmation via Telegram before execution. The operator reviews the reasoning, the risk assessment, and the proposed parameters -- then approves or rejects.

AI proposes. Humans decide.

### Tweet 5 -- Call to Action

RUNECLAW is open for review and feedback.

GitHub: https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER
Documentation: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
Telegram: https://t.me/+VRNgsmkR5pszZTdk

Built by Humanoid Traders for the Bitget hackathon. Read the code. Read the docs. Tell us what we missed.

---

## Discord / Community Post

**RUNECLAW -- AI Trading Command Core | Hackathon Submission**

We are releasing RUNECLAW, a simulation-first AI trading agent designed for the Bitget ecosystem. This post covers what it does, how it works, and where to find the code.

**What it does:**
RUNECLAW scans Bitget markets for volume anomalies and momentum shifts, generates trade ideas using a 6-indicator confluence model augmented by LLM reasoning, and enforces a strict pre-trade risk protocol before any order is proposed to the operator.

**How it works:**
The system operates as a 9-state finite state machine. Each trade passes through a defined lifecycle: market scan, analysis, risk evaluation, human confirmation, execution, monitoring, and cooldown. Every state transition is logged. Every risk check is evaluated independently in a fail-closed design -- if any check cannot return a definitive result, the trade is blocked.

The risk engine runs 16 independent checks covering position sizing, portfolio exposure, asset correlation, drawdown limits, volatility conditions, and cooldown periods. A circuit breaker halts the system entirely if predefined loss thresholds are hit.

**What makes it different:**
- Regime-aware analysis using ADX-14 to classify market conditions as trending, ranging, or choppy
- Institutional-grade risk controls that cannot be bypassed by the trading logic
- Complete audit trail of every decision the system makes
- Paper-trading by default with dual safety flags for live trading
- Human-in-the-loop confirmation via Telegram for every proposed trade

**Built with:**
Python, Bitget API, Telegram Bot API, Pydantic strict schemas

**We are looking for:**
- Code review and architectural feedback
- Suggestions on additional risk checks or edge cases
- Stress testing of the backtesting engine
- General feedback on the approach

All code and documentation are available at the links below. We welcome technical critique.

GitHub: https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER
Documentation: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
Telegram: https://t.me/+VRNgsmkR5pszZTdk

---

## One-Liner Pitch

RUNECLAW is a simulation-first AI trading agent that enforces 15 fail-closed risk checks and requires human confirmation before every trade.

---

## Suggested Hashtags

#RUNECLAW #BitgetHackathon #TradingAgent #AITrading #AlgoTrading #RiskManagement #HumanInTheLoop #CryptoTrading #TradingInfra #OpenSource #SimulationFirst #QuantTrading
