# RUNECLAW: Case Study

## Bitget GetClaw / Agent Hub Genesis S1 Hackathon Submission

**Team:** Humanoid Traders
**Track:** Trading Agent + Trading Infra

---

## Strategy Summary

RUNECLAW is an AI trading agent that treats risk management as the product, not a feature bolted on at the end. Where most trading bots optimize for signal generation and treat risk as an afterthought, RUNECLAW inverts the priority: the risk engine has absolute veto power, and no trade executes without clearing every gate.

The system scans all Bitget USDT pairs for volume anomalies and momentum shifts, then generates trade ideas through a 10-voter confluence scoring model that blends six classical technical indicators (RSI-14, MACD, Bollinger Bands, ADX-14, VWAP, Volume Spike) with four structural signals (On-Balance Volume trend, candlestick pattern detection across 14 patterns, Fibonacci retracement zone classification, and LLM-powered directional reasoning). The technical score and LLM confidence are combined at a 60/40 weighting to produce a final conviction score.

What makes RUNECLAW different is the non-negotiable pipeline: every trade idea passes through 18 independent fail-closed risk checks, then waits for human confirmation via Telegram inline keyboard, then re-checks risk at confirmation time because the market may have moved. Paper trading is the default mode with a $10K virtual balance. Live trading requires two explicit environment flags -- you cannot accidentally trade real money.

The philosophy is simple: AI proposes. Humans decide. The risk engine enforces.

## Architecture & Innovation

RUNECLAW is built around a **9-state finite state machine** that governs the complete trade lifecycle:

**IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING -> COOLING_DOWN / HALTED**

Every state transition is validated and logged. The FSM prevents impossible sequences -- you cannot execute without passing risk check, you cannot monitor without having executed, and the HALTED state (triggered by the circuit breaker) blocks all new activity until manual reset.

**10-Voter Confluence Model.** The analysis engine assembles votes from 10 independent signal sources: RSI-14, MACD (12/26/9), Bollinger %B, Volume Spike detection, ADX-14 directional strength, VWAP alignment, OBV trend, candlestick pattern signal (14 patterns including engulfing, harami, morning/evening star, three white soldiers/crows), Fibonacci retracement zone classification (23.6% through 78.6% with swing detection over 50-bar lookback), and LLM confidence. SMA-50 trend alignment adds a bonus/penalty (+0.10/-0.15), and volume confirmation provides an additional +/-0.05 adjustment. The result is a structured `TradeIdea` with entry, stop-loss, take-profit, confidence score, and human-readable reasoning.

**ADX-14 Regime Detection.** The market is classified into four regimes: TREND_UP, TREND_DOWN, RANGE, and CHOP. The regime directly affects strategy behavior -- trending regimes skip counter-trend signals, RANGE and CHOP apply confidence penalties, and ATR-based stop/take-profit multipliers adapt to volatility (high vol: 3.0/4.5x ATR, normal: 2.5/3.5x, low: 2.0/3.0x).

**Macro Calendar Awareness.** A macro event calendar tracks 10 event types (FOMC, CPI, PPI, NFP, GDP, unemployment claims, PCE, ISM, retail sales, housing data) through a 5-state risk machine (NORMAL -> PRE_EVENT_CAUTION -> EVENT_LOCKDOWN -> POST_EVENT_VOLATILITY -> BLACKOUT). As high-impact events approach, the risk machine blocks new trades during the lockdown window and can trigger full blackout periods where no new trades are permitted.

**MCP Tool Server.** RUNECLAW exposes 8 tools via Model Context Protocol for Agent Hub integration: market scan, asset analysis, portfolio status, risk dashboard, trade confirmation, backtest execution, rejected trade history, and emergency halt. This allows any MCP-compatible agent (including GetClaw ecosystem agents) to interact with RUNECLAW programmatically.

## Risk Management Deep Dive

The risk engine is the core of RUNECLAW. It enforces **18 independent fail-closed checks** -- every single one must pass for a trade to proceed. If any check fails, or if any check throws an exception during evaluation, the trade is rejected. There are no overrides, no admin bypasses, no "just this once" escape hatches.

The 18 checks are:

1. **Position size validation** -- fixed-fractional (risk_budget / stop_distance), capped at 20% notional
2. **Daily loss limit** -- 5% of starting equity
3. **Max drawdown** -- 10% from peak equity
4. **Max open positions** -- configurable cap
5. **Risk/reward ratio** -- minimum 1.2x required
6. **Confidence threshold** -- minimum 60% conviction score
7. **Correlation group blocking** -- prevents overconcentration in correlated assets
8. **Consecutive loss streak** -- triggers cooldown after N losses
9. **Entry price sanity** -- validates entry vs. current market price
10. **Stop-loss required** -- no trade without a defined stop
11. **Stale data guard** -- rejects ideas older than 5 minutes
12. **Cooldown timer** -- enforces minimum time between trades
13. **Portfolio exposure** -- total portfolio risk budget
14. **Per-symbol exposure** -- 20% max per individual asset
15. **Volatility guard** -- ATR-based, rejects during extreme volatility
16. **Circuit breaker** -- auto-halts on cascade failures
17. **Macro event guard** -- blocks trades during high-impact economic events
18. **Liquidity guard** -- validates sufficient order book depth

The **fail-closed philosophy** is the key differentiator. Most trading systems are fail-open: if a risk check errors out, the trade proceeds on the assumption that missing data is acceptable. RUNECLAW takes the opposite stance. An exception in any check is treated as a rejection. This means the system degrades toward safety, never toward risk.

The **circuit breaker** trips on three conditions: 5% daily loss, 10% drawdown from peak, or 5 consecutive losing trades. Once tripped, the agent enters the HALTED state and requires manual reset -- it will not auto-recover and start trading again.

**Trailing stops** activate at 1R profit (when unrealized gain equals the initial risk) and trail at 1.5x ATR behind the best price. In backtesting, trailing stops accounted for 48.7% of all exits with net-positive aggregate PnL. This is by construction -- trailing stops that activate at +1R and trail 1.5 ATR structurally lock in at least 1 ATR of profit. We claim structural soundness, not predictive edge.

**Re-check on confirmation.** When a human taps "Confirm" on the Telegram keyboard, the risk engine runs all 18 checks again against current market conditions. Markets move. A trade that was safe 30 seconds ago may no longer be safe. This second pass catches drift.

## Backtest Evidence

RUNECLAW was validated across **500 backtest runs** using synthetic market data generated with Geometric Brownian Motion + GARCH volatility modeling. The synthetic data covered 5 market regimes (Bull, Bear, Range/Chop, High Volatility, Crash Recovery), 20 symbols, and 5 random seeds per combination.

**Key metrics across 500 runs:**
- **889 total trades** generated and evaluated (485 valid, 15 errors)
- **50.5% win rate** (in line with trend-following expectations)
- **Worst-case drawdown: 3.87%** (well within the 10% circuit breaker threshold)
- **Best run: +8.06%**, avg return -0.46%
- **Zero crashed runs** -- all 500 completed without exceptions
- **Trailing stop exits: 48.7%** of all exits, net-positive aggregate PnL
- **Profit factor > 1** across the majority of runs

**What the numbers prove:** The risk engine works. Across 889 trades spanning wildly different market conditions, the system never breached its safety limits. Drawdown stayed controlled. No single run produced a catastrophic loss. The trailing stop mechanism exits profitably by construction.

**What the numbers do NOT prove:** Predictive edge in live markets. The backtest uses synthetic data, not historical market data. Commission and slippage are modeled (0.1% and 0.05% respectively), but real exchange conditions involve latency, partial fills, and liquidity gaps that synthetic data cannot capture. The 50.5% win rate is modest and honest -- RUNECLAW is not claiming alpha generation. It is claiming that when it trades, it does so within disciplined risk parameters that prevent blowups.

**Honest limitation:** No amount of synthetic backtesting substitutes for live market validation. These results demonstrate that the risk architecture functions as designed under stress, not that the strategy will be profitable in production.

## Technical Differentiators

What RUNECLAW has that no other hackathon entry offers:

**18 mandatory risk gates.** Not 3, not 5 -- eighteen independent checks that all must pass. Most hackathon trading agents have a position size check and maybe a stop-loss. RUNECLAW has correlation blocking, stale data guards, volatility guards, macro event awareness, and a circuit breaker with cooldown enforcement. Every gate is fail-closed.

**Full JSONL audit trail.** Three structured log channels (trade, risk, system) record every decision with timestamps. Every rejection includes which check failed and why. This is not debug logging -- it is a compliance-grade audit trail that can be replayed for post-mortem analysis.

**Human-in-the-loop by default.** Every trade requires explicit human confirmation via Telegram inline keyboard. There is no autonomous execution mode. The human sees the trade idea, the risk assessment, and the reasoning before deciding. This is not optional -- it is architecturally enforced.

**Macro calendar integration.** A 10-event-type economic calendar (FOMC, CPI, NFP, GDP, etc.) feeds into a 5-state risk machine (NORMAL, PRE_EVENT_CAUTION, EVENT_LOCKDOWN, POST_EVENT_VOLATILITY, BLACKOUT) that blocks new trades during lockdown and blackout windows. During BLACKOUT periods, no new trades are permitted regardless of signal quality.

**Portfolio persistence.** Portfolio state is persisted to JSON on every state change. If the bot crashes and restarts, open positions, equity history, and risk state are restored. No orphaned positions, no lost state.

**MCP tool server.** 8 tools exposed via Model Context Protocol, making RUNECLAW a first-class citizen in the GetClaw Agent Hub ecosystem. Any MCP-compatible agent can scan markets, request analysis, check risk status, or trigger emergency halts through RUNECLAW.

**180 unit tests.** Covering risk engine edge cases, portfolio state transitions, analyzer indicators (including candlestick patterns, Fibonacci, OBV), backtest replay integrity, and integration scenarios. The test suite is the specification.

## Reflection

Building RUNECLAW taught us that the hardest part of a trading agent is not generating signals -- it is saying no. The risk engine took more development time than the analysis engine, and that ratio was correct. Every shortcut we considered in risk management would have created a path to catastrophic failure.

If we could start over, we would invest earlier in historical data integration alongside synthetic backtesting. We would build a more sophisticated order execution layer with TWAP/VWAP splitting. We would add multi-timeframe confluence across 5m/15m/1h/4h charts rather than single-timeframe analysis.

The biggest lesson: fail-closed is not just a design pattern. It is a commitment to humility -- an acknowledgment that the system does not know what it does not know, and when in doubt, the safe answer is always "no."

---

**Links:**
- GitHub: https://github.com/Humanoid-Traders/RUNECLAW
- Website: https://lgl3crf9.mule.page/
- GitBook: https://humanoid-traders-1.gitbook.io/humanoid-traders-ai
- Telegram: https://t.me/+VRNgsmkR5pszZTdk
- X: https://x.com/BaurPatric70363

---

#BitgetAIAgent #GenesisS1
