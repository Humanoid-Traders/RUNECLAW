# FAQ

## General

**What is RUNECLAW?**

RUNECLAW is an AI-powered trading command core built for the Bitget GetClaw Hackathon. It scans markets, generates trade ideas using AI analysis, enforces strict risk limits, and executes paper trades -- all through a Telegram bot interface.

**Does RUNECLAW trade with real money?**

No, not by default. RUNECLAW starts in simulation mode with paper trading enabled. Live trading requires explicitly setting two environment flags (`SIMULATION_MODE=false` and `LIVE_TRADING_ENABLED=true`). This two-key mechanism prevents accidental activation.

**What exchange does it support?**

RUNECLAW is built for Bitget. It connects via the ccxt library and scans all USDT trading pairs. The exchange connection defaults to sandbox mode.

---

## Setup

**Do I need a Bitget account?**

For real market data, yes. You need API credentials from Bitget. However, the system can run in CLI mode for testing without exchange credentials (some features will be limited).

**Do I need an OpenAI API key?**

No. If no LLM API key is configured, the analyzer falls back to a rule-based strategy using RSI thresholds. The LLM enhances analysis quality but is not required.

**What Python version is required?**

Python 3.11 or newer.

**How do I get a Telegram bot token?**

Message [@BotFather](https://t.me/BotFather) on Telegram, use the `/newbot` command, and follow the prompts. You will receive a token to set as `TELEGRAM_BOT_TOKEN`.

---

## Trading

**How does the AI generate trade ideas?**

The analyzer computes technical indicators (RSI-14, MACD, Bollinger Bands, ATR, ADX-14, VWAP, OBV, Fibonacci retracement levels, candlestick patterns, SMA-50) from hourly candles and sends them to an LLM along with market context. The LLM returns a directional call (LONG/SHORT), confidence score, and reasoning. This is structured into a `TradeIdea` with computed entry, stop-loss, and take-profit levels.

**What is the minimum confidence for a trade?**

0.60 (60%). Ideas below this threshold are discarded at the analyzer level and double-checked at the risk engine level.

**What is the minimum risk/reward ratio?**

1.2x. The risk engine rejects any trade where the potential reward is less than 1.2 times the potential risk.

**Can I override a risk rejection?**

No. The risk engine has no override mechanism. This is by design. If you want to adjust limits, change the environment variables (`MAX_POSITION_PCT`, `MAX_DAILY_LOSS_PCT`, etc.) and restart.

---

## Risk

**What triggers the circuit breaker?**

Two conditions:
1. Daily realized loss exceeds `MAX_DAILY_LOSS_PCT` (default: 5%).
2. Portfolio drawdown from peak exceeds `MAX_DRAWDOWN_PCT` (default: 10%).

**How do I reset the circuit breaker?**

The circuit breaker requires manual reset. This is intentional to ensure a human reviews the situation before trading resumes. Currently this is done programmatically via `engine.risk.reset_circuit_breaker()`.

**What are the default risk limits?**

| Parameter | Default |
|-----------|---------|
| Max position size | 2% of equity |
| Max daily loss | 5% of balance |
| Max drawdown | 10% from peak |
| Max open positions | 5 |
| Min risk/reward | 1.2x |
| Min confidence | 60% |

---

## Paper Trading

**What is the default paper balance?**

$10,000. Configurable via the `PAPER_BALANCE_USD` environment variable.

**Is portfolio state persisted?**

Yes. Portfolio state is automatically saved to `data/portfolio_state.json` after every trade execution. On restart, the tracker loads the last saved state. If the file is missing or corrupted, it starts fresh with the default balance.

**Does it simulate fees or slippage?**

Paper trades execute at exact signal prices with no fees in live paper mode. However, the **backtest engine** models both commission (0.1%) and slippage (0.05%). Commission is computed once by the portfolio tracker to avoid double-counting.

---

## Technical

**What indicators does the analyzer compute?**

- RSI-14 (Relative Strength Index)
- MACD (12, 26, 9) with signal line
- Bollinger Bands (20-period, 2 standard deviations)
- ATR (Average True Range proxy from close prices)
- ADX-14 (Average Directional Index for regime detection)
- VWAP and anchored VWAP
- OBV (On-Balance Volume trend)
- Fibonacci retracement levels (23.6%, 38.2%, 50%, 61.8%, 78.6% over 50-bar swing)
- Candlestick patterns (14 patterns including doji, hammer, engulfing, morning/evening star)
- SMA-50 (trend alignment filter)

**How is volume spike detection implemented?**

The scanner maintains a 20-period rolling window of volume for each symbol. If the current volume exceeds 2x the rolling average, it is flagged as a spike.

**What logging format is used?**

Structured JSON lines (JSONL). Each line is a self-contained JSON object with timestamp, level, channel, message, action, result, and optional data. Three separate files: `trade.jsonl`, `risk.jsonl`, `system.jsonl`.

**Can I add custom skills?**

Yes. Subclass `BaseSkill`, implement the `execute` method, and register it with the `SkillRegistry`. See the [API Reference](api-reference.md) for details.
