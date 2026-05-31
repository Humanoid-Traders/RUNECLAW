# Telegram Confirmation Flow

Every trade RUNECLAW proposes must be approved by a human before execution. This page describes the full confirmation lifecycle, from trade idea generation to final execution or rejection.

---

## Why Human-in-the-Loop?

RUNECLAW is designed around a core principle: **AI proposes, humans decide.**

The AI generates trade ideas using technical analysis, LLM reasoning, and confluence scoring. The risk engine filters out bad ideas. But the final decision always belongs to the operator.

This prevents:
- Runaway automated trading during unusual market conditions
- Execution of trades the operator disagrees with
- Loss of control over capital allocation

---

## Flow Diagram

```text
/analyze BTC
      |
      v
  Analyzer: compute indicators, LLM thesis, TradeIdea
      |
      v
  Risk Engine: 18 fail-closed checks
      |
      ├── ANY check fails → REJECTED (logged, operator notified)
      |
      v
  TradeIdea added to pending queue
      |
      v
  Telegram message sent with inline keyboard:
  ┌─────────────────────────────────────────┐
  │  TRADE IDEA — LONG BTC/USDT             │
  │  Confidence: 72% | R:R: 2.8             │
  │  Entry: $108,420 | SL: $107,200         │
  │                                          │
  │  [✅ Confirm]    [❌ Reject]              │
  └─────────────────────────────────────────┘
      |                    |
      v                    v
  CONFIRM              REJECT
      |                    |
      v                    v
  Risk RE-CHECK        Idea discarded
  (18 checks again)    Audit log entry
      |
      ├── RE-CHECK fails → REJECTED (market moved)
      |
      v
  Paper trade executed
  Position recorded in portfolio
  Audit log entry
```

---

## Step-by-Step Walkthrough

### 1. Operator requests analysis

The operator sends `/analyze BTC` (or any symbol) in the Telegram chat. The bot acknowledges and begins the pipeline.

### 2. AI generates trade idea

The analyzer:
- Fetches OHLCV candles from Bitget
- Computes 10+ technical indicators (RSI, MACD, Bollinger, ADX, VWAP, OBV, candlestick patterns, Fibonacci levels)
- Detects market regime (TREND_UP, TREND_DOWN, RANGE, CHOP)
- Generates an LLM-powered directional thesis (or falls back to rule-based scoring)
- Produces a structured `TradeIdea` with entry, stop-loss, take-profit, confidence, and reasoning

Ideas with blended confidence below 60% are discarded before reaching the risk gate.

### 3. Risk gate evaluates the idea

The risk engine runs 18 independent checks. Every check must pass:

- Position size, daily loss, drawdown, max positions
- Risk/reward ratio, confidence threshold
- Correlation blocking, loss streak detection
- Entry price sanity, stop-loss required
- Stale data guard, cooldown timer
- Portfolio exposure, per-symbol exposure
- Volatility guard, circuit breaker
- Liquidity guard, macro event gate

If **any single check fails**, the trade is rejected immediately. The operator sees a rejection message with the specific check that failed. No override is possible.

### 4. Telegram inline keyboard

If the idea passes all 18 checks, the bot sends a message with two inline buttons:

- **Confirm** -- approve the trade for execution
- **Reject** -- discard the trade idea

The message includes: direction (LONG/SHORT), asset, confidence percentage, risk/reward ratio, entry price, stop-loss, and take-profit levels.

The operator can also view all pending ideas at any time with the `/trade` command.

### 5. Confirmation and re-check

When the operator taps **Confirm**:

1. The risk engine runs all 18 checks **again** against the current portfolio state
2. This catches scenarios where:
   - Another trade was confirmed between idea generation and confirmation
   - Daily loss limit was reached by a closed position
   - Market conditions changed (stale data guard)
   - A macro event entered the lockdown window
3. If the re-check passes, the trade executes
4. If the re-check fails, the confirmation is rejected with an explanation

This re-check is critical because time passes between idea generation and human decision. The market state at confirmation time may differ from analysis time.

### 6. Rejection

When the operator taps **Reject**:

1. The trade idea is removed from the pending queue
2. An audit entry is logged with the rejection reason ("human_rejected")
3. No further action is taken

### 7. Execution

After a successful confirmation and re-check:

- **Paper mode:** The portfolio tracker opens a position with the specified entry, stop-loss, and take-profit. PnL is tracked mark-to-market.
- **Live mode:** (If enabled) An order is placed on Bitget via ccxt. The same portfolio tracking applies.

The engine then enters the MONITORING state, continuously checking open positions against current prices for stop-loss and take-profit exits.

---

## Authorization

Not everyone can confirm trades. The Telegram handler enforces authorization:

| Setting | Behavior |
|---------|----------|
| `TELEGRAM_CHAT_ID` set | Only messages from listed chat IDs are processed |
| `TELEGRAM_CHAT_ID` empty, `TELEGRAM_ALLOW_OPEN=true` | Any user can interact (development only) |
| `TELEGRAM_CHAT_ID` empty, `TELEGRAM_ALLOW_OPEN` unset | All commands rejected (fail-closed) |

Authorization applies to both slash commands and inline keyboard callbacks. An unauthorized user cannot confirm or reject trades.

---

## Rate Limiting

Every Telegram command is rate-limited per user:

| Parameter | Default | Description |
|-----------|---------|-------------|
| Rate limit | 20 requests/minute | Per-user sliding window |

If a user exceeds the limit, subsequent commands are rejected with a warning until the window expires. This prevents abuse and protects the Bitget API from excessive calls.

---

## Audit Trail

Every step in the confirmation flow is logged as structured JSON:

```json
{"timestamp": "2026-05-29T14:23:01Z", "action": "trade_idea_generated", "asset": "BTC/USDT", "direction": "LONG", "confidence": 0.72}
{"timestamp": "2026-05-29T14:23:01Z", "action": "risk_check", "result": "APPROVED", "checks_passed": 18}
{"timestamp": "2026-05-29T14:23:15Z", "action": "telegram_callback", "data": "confirm:TI-a1b2c3d4"}
{"timestamp": "2026-05-29T14:23:15Z", "action": "risk_recheck", "result": "APPROVED", "checks_passed": 18}
{"timestamp": "2026-05-29T14:23:15Z", "action": "trade_executed", "asset": "BTC/USDT", "mode": "PAPER", "entry": 108420.0}
```

Logs are written to `logs/trade.jsonl`, `logs/risk.jsonl`, and `logs/system.jsonl`. Every decision, confirmation, and rejection is traceable.
