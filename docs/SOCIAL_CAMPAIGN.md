# RUNECLAW Social Media Campaign

Target: Community Impact Award

---

## One-Liner Pitch

RUNECLAW is a simulation-first AI trading agent with 18 risk risk checks, regime-aware analysis, and human confirmation on every trade.

---

## Twitter/X Thread

### Tweet 1 -- Launch

Introducing RUNECLAW -- an AI trading agent built for the Bitget ecosystem.

Track 1 (Trading Agent) | Track 2 (Trading Infra)

18 independent risk checks. Fail-closed architecture. Every trade requires human confirmation. Paper trading by default.

Thread below.

### Tweet 2 -- Risk Engine

RUNECLAW enforces 18 pre-trade risk checks. Every single one must pass, or the trade is rejected. There is no override.

Position limits. Drawdown caps. Correlation blocking. Per-symbol exposure. Volatility guard. Cooldown timers. Circuit breaker.

One failure = rejected. No exceptions.

### Tweet 3 -- Backtest Results

500-run stress test across 5 market regimes, 20 symbols, 5 seeds:

- 889 trades analyzed
- 50.5% win rate
- Worst drawdown: 3.87%
- Zero crashed runs
- Trailing stops: 48.7% of all exits, net-positive aggregate PnL

No inflated claims. These are the actual numbers from synthetic data backtesting.

### Tweet 4 -- Explainability

Every decision RUNECLAW makes is logged and auditable.

State transitions. Risk gate evaluations. Indicator scores. Confluence model weights. LLM reasoning. Execution outcomes.

If the system rejects a trade, you can trace exactly which of the 18 checks failed and why. No black boxes.

### Tweet 5 -- Regime Detection

RUNECLAW doesn't trade blindly. ADX-14 classifies market conditions:

- TREND_UP / TREND_DOWN -- trade with the trend
- RANGE -- apply confidence penalty, tighten targets
- CHOP -- heavier penalty, conservative sizing

Strategy adapts stop-loss and take-profit multipliers to current volatility. Not a fixed formula.

### Tweet 6 -- Simulation-First

RUNECLAW ships in paper-trading mode. $10,000 virtual balance. Full position lifecycle with PnL tracking.

Two independent safety flags must be toggled before any real capital is at risk. Every trade requires human confirmation via Telegram.

AI proposes. Humans decide.

### Tweet 7 -- Call to Action

RUNECLAW is open for review.

GitHub: https://github.com/Humanoid-Traders/RUNECLAW
Website: https://lgl3crf9.mule.page/
Docs: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
Telegram: https://t.me/+VRNgsmkR5pszZTdk
X: https://x.com/BaurPatric70363

Built by Humanoid Traders. 180 tests. 18 risk checks. Read the code.

---

## Discord / Community Post

**RUNECLAW -- AI Trading Command Core | Hackathon Submission**

We are submitting RUNECLAW, a simulation-first AI trading agent built for the Bitget ecosystem.

**What it does:**
RUNECLAW scans markets for volume anomalies and momentum shifts, generates explainable trade ideas using a 10-voter confluence model (RSI, MACD, BB, Volume Spike, ADX, VWAP, OBV trend, candlestick pattern detection, Fibonacci retracement zone) blended with LLM reasoning, and enforces 18 independent pre-trade risk checks before proposing any trade to the operator.

**Architecture:**
- 9-state finite state machine (IDLE through HALTED)
- 18 risk risk checks -- any failure blocks the trade
- ADX-14 regime detection adapts strategy to trend/range/chop conditions
- Adaptive ATR-based stop-loss and take-profit scaling
- Trailing stops accounted for 48.7% of all exits with net-positive aggregate PnL
- Thread-safe across all shared state with RLock

**Backtest validation:**
500 runs across 5 market regimes, 20 symbols, 5 seeds. 889 total trades. Worst drawdown 3.87%. Zero crashed runs. Trailing stops lock in profit by construction (activate at +1R, trail 1.5 ATR).

**Safety design:**
- Paper trading by default, live requires dual flag opt-in
- Human confirmation via Telegram for every trade
- Circuit breaker halts system on 5% daily loss or 10% drawdown
- Cooldown timer after losses prevents revenge trading
- Per-symbol and portfolio-level exposure limits

**Test coverage:**
180 unit tests covering risk engine (all 18 checks), portfolio lifecycle, analyzer indicators (including candlestick pattern detection, Fibonacci retracement, OBV, rolling VWAP), backtest replay, macro calendar states, integration scenarios, edge cases, and negative inputs.

**Links:**
- GitHub: https://github.com/Humanoid-Traders/RUNECLAW
- Website: https://lgl3crf9.mule.page/
- Docs: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
- Telegram: https://t.me/+VRNgsmkR5pszZTdk
- X: https://x.com/BaurPatric70363

We welcome code review, architectural feedback, and stress testing suggestions.

---

## Suggested Hashtags

#RUNECLAW #BitgetHackathon #TradingAgent #AITrading #AlgoTrading #RiskManagement #HumanInTheLoop #CryptoTrading #TradingInfra #OpenSource #SimulationFirst #QuantTrading
