# Architecture

This document describes the internal architecture of RUNECLAW, how data flows through the system, and how each component interacts.

## High-Level Overview

```
                     +-------------------+
                     |   Telegram Bot    |
                     |  (User Commands)  |
                     +--------+----------+
                              |
                     +--------v----------+
                     |  Skill Registry   |
                     | (Command Router)  |
                     +--------+----------+
                              |
                     +--------v----------+
                     |  RuneClaw Engine  |
                     |  (Orchestrator)   |
                     +--+-----+------+--+
                        |     |      |
           +------------+     |      +------------+
           |                  |                   |
  +--------v-------+  +------v-------+  +--------v-------+
  | Market Scanner  |  |  AI Analyzer |  |  Risk Engine   |
  | (Bitget/ccxt)   |  | (LLM + TA)  |  | (18 Checks)   |
  +----------------+  +--------------+  +--------+-------+
                                                  |
                                         +--------v-------+
                                         |   Portfolio    |
                                         |   Tracker      |
                                         +----------------+
```

## Pipeline Stages

The engine operates in a continuous loop with four stages:

### Stage 1: SCAN

The `MarketScanner` connects to Bitget via ccxt and fetches all USDT-pair tickers. It then:

1. Filters out pairs with less than $50,000 in 24h volume.
2. Calculates a momentum score based on 24h price change.
3. Detects volume spikes by comparing current volume to a 20-period rolling average (threshold: 2x).
4. Ranks all signals by absolute momentum and returns the top N (default: 10).

Output: a list of `MarketSignal` objects.

### Stage 2: ANALYZE

The `Analyzer` receives the top 3 signals and for each:

1. Fetches 100 hourly OHLCV candles from Bitget.
2. Computes technical indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR-14, ADX-14, VWAP, On-Balance Volume (OBV), and Anchored VWAP (20-bar and 50-bar variants).
3. Detects 14 candlestick patterns: doji, hammer, shooting star, spinning top, marubozu, bullish/bearish engulfing, bullish/bearish harami, tweezer top/bottom, morning star, evening star, three white soldiers, and three black crows.
4. Computes Fibonacci retracement levels from swing high/low over a 50-bar lookback, classifying price into standard zones (23.6%, 38.2%, 50%, 61.8%, 78.6%).
5. Detects market regime via ADX (TREND_UP, TREND_DOWN, RANGE, CHOP).
6. Scores confluence across 10 voters (RSI, MACD, Bollinger %B, Volume Spike, ADX, VWAP, OBV trend, candlestick pattern signal, Fibonacci zone, plus the original 6 expanded to 10).
7. Sends the signal data and indicators to the LLM for a directional thesis.
8. If no LLM key is configured, falls back to a rule-based confluence strategy.
9. Structures the result as a `TradeIdea` with entry, stop-loss, take-profit, confidence, and reasoning.
10. Filters out ideas with blended confidence below 0.60.

Output: a `TradeIdea` object (or None if conviction is too low).

### Stage 3: RISK GATE

Every `TradeIdea` is passed to the `RiskEngine` for 20 independent pre-trade checks:

1. Circuit breaker status
2. Position size vs. max notional %
3. Daily loss vs. daily loss limit
4. Portfolio drawdown vs. max drawdown
5. Open positions count vs. max positions
6. Risk/reward ratio (minimum 1.2)
7. Confidence threshold (minimum 0.60)
8. Correlation / concentration per group
9. Consecutive loss streak (>= 3 rejects)
10. Entry price sanity
11. Stop-loss required
12. Stale data guard
13. Cooldown after loss
14. Portfolio exposure limit
15. Per-symbol exposure limit
16. Volatility guard (ATR-based)
17. Liquidity guard (order book depth, fail-open)
18. Macro event gate (FOMC, CPI, NFP lockdown)

If ANY check fails, the trade is **REJECTED**. There are no overrides.

If the circuit breaker triggers (daily loss or drawdown breach), it remains active until manually reset.

### Stage 4: HUMAN CONFIRM

Approved trade ideas are placed in a pending queue. The Telegram bot displays them with inline **Confirm** / **Reject** buttons.

On confirmation:
1. Risk is **re-evaluated** (market may have moved).
2. If still approved, the paper trade is executed.
3. The portfolio tracker records the position.

On rejection:
1. The idea is removed from the pending queue.
2. An audit entry is logged.

### Stage 5: MONITOR

The engine continuously checks open positions against current market prices. If a stop-loss or take-profit level is hit, the position is automatically closed and PnL is recorded.

## Data Models

All data flowing through the system uses strict Pydantic v2 models:

| Model | Purpose |
|-------|---------|
| `MarketSignal` | Scanner output -- price, volume, momentum |
| `TradeIdea` | Analyzer output -- entry, SL, TP, confidence, reasoning |
| `RiskCheck` | Risk engine output -- verdict, checks passed/failed |
| `TradeExecution` | Execution record -- position details, PnL |
| `PortfolioState` | Portfolio snapshot -- balance, equity, drawdown |

Models are mutable Pydantic BaseModel instances. Mutation is discouraged outside the owning module but not enforced by `frozen=True`.

## Logging Architecture

Three independent log channels write structured JSON (JSONL format):

| Channel | File | Content |
|---------|------|---------|
| `runeclaw.trade` | `logs/trade.jsonl` | Trade ideas, executions, closures |
| `runeclaw.risk` | `logs/risk.jsonl` | Risk checks, circuit breaker events |
| `runeclaw.system` | `logs/system.jsonl` | Engine lifecycle, scan results, errors |

Every log entry includes: timestamp, level, channel, message, action, result, and optional structured data.

## Configuration

All configuration is loaded from environment variables with safe defaults. The `AppConfig` dataclass nests four sub-configs:

- `RiskLimits` -- position sizing, loss limits, drawdown caps
- `ExchangeConfig` -- Bitget API credentials (sandbox by default)
- `TelegramConfig` -- bot token, chat ID, rate limits
- `LLMConfig` -- API key, model name, temperature

See `.env.example` for the full list of configurable values.

---

## AI Learning System

RUNECLAW includes an adaptive AI learning system with **8 modules** that enable the bot to improve over time:

| Module | Purpose |
|--------|---------|
| **Experience Memory** | Stores trade outcomes (entry, exit, PnL, market conditions) for pattern mining |
| **Reflection Engine** | Periodically reviews recent trades to identify systematic errors and biases |
| **Strategy Evaluator** | Grades strategy variants on a **S/A/B/C/D tier** scale based on backtest and live performance |
| **Pattern Learner** | Detects recurring market patterns (candlestick sequences, indicator confluences) that preceded profitable trades |
| **Macro Learner** | Correlates macro event outcomes (e.g., CPI surprises) with post-event price behavior |
| **Model Comparer** | Benchmarks multiple LLM models and prompt templates against each other on the same trade ideas |
| **Prompt Optimizer** | Evolves LLM prompt templates by testing variations and selecting for higher-quality trade theses |
| **Feedback Collector** | Gathers operator feedback on trade proposals to incorporate human judgment into the learning loop |

### Safety Policy

The learning system enforces a **blocked-actions policy** that prevents it from:

- Modifying risk engine parameters or thresholds
- Bypassing the 20-check risk gate
- Executing trades without human confirmation
- Altering circuit breaker state

All learning outputs are advisory. They inform the operator and tune analysis parameters -- they never override safety mechanisms.

---

## LLM Token Optimizer

To control LLM API costs, RUNECLAW implements a **4-layer token optimization system** that achieves up to **70% cost savings** compared to naive per-call invocation:

| Layer | Mechanism | Savings |
|-------|-----------|---------|
| **Semantic Cache** | Caches LLM responses keyed by a semantic hash of the input (indicators + market context). Near-identical market conditions return cached results instead of making a new API call. | High |
| **Tiered Pipeline** | Routes simple analyses to cheaper/smaller models and reserves expensive models for complex or high-confidence-required scenarios. | Medium |
| **Smart Batching** | Batches multiple analysis requests into a single LLM call when multiple assets are queued for analysis in the same cycle. | Medium |
| **Adaptive Frequency** | Dynamically adjusts analysis frequency based on market volatility -- scans more often during high-volatility periods and less often during quiet markets. | Variable |

The optimizer is transparent to the rest of the system. The analyzer always receives a trade thesis regardless of whether it came from cache, a cheap model, or a premium model.

---

## Macro Event Calendar

The macro calendar tracks **2026 FOMC, CPI, NFP, and PCE schedules** and feeds them into the risk engine's macro event gate (check #18). The calendar drives a **5-state risk machine**:

| State | Meaning |
|-------|---------|
| `NORMAL` | No upcoming events within 24 hours. No restrictions. |
| `PRE_EVENT_CAUTION` | An event is within 24 hours. Informational warning logged. |
| `EVENT_LOCKDOWN` | 30 minutes before to 30 minutes after the event. **All trades rejected.** |
| `POST_EVENT_VOLATILITY` | 30 minutes to 4 hours after the event. Warning logged. |
| `BLACKOUT` | Calendar evaluation failed. **All trades rejected** (fail-closed). |

The calendar is fail-closed by design: if event data cannot be loaded or parsed, the system enters `BLACKOUT` and blocks all trading until the issue is resolved.
