```
 ____  _   _ _   _ _____ ____ _        ___        __
|  _ \| | | | \ | | ____/ ___| |      / \ \      / /
| |_) | | | |  \| |  _|| |   | |     / _ \ \ /\ / /
|  _ <| |_| | |\  | |__| |___| |___ / ___ \ V  V /
|_| \_\\___/|_| \_|_____\____|_____/_/   \_\_/\_/
```

<h3 align="center">AI Trading Command Core | Forged in Volatility. Governed by Discipline.</h3>
<h4 align="center">by Humanoid Traders | for Bitget GetClaw</h4>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue?logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
  <img src="https://img.shields.io/badge/mode-paper%20trading-orange" alt="Paper Trading">
  <img src="https://img.shields.io/badge/exchange-Bitget-blue" alt="Bitget">
  <img src="https://img.shields.io/badge/hackathon-GetClaw%202025-purple" alt="GetClaw Hackathon">
</p>

<p align="center">
  <a href="https://github.com/Humanoid-Traders/RUNECLAW">GitHub</a> &middot;
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai">Documentation</a> &middot;
  <a href="https://t.me/+VRNgsmkR5pszZTdk">Telegram</a>
</p>

---

## What is RUNECLAW?

**RUNECLAW** is an AI trading command system built by **Humanoid Traders** for the Bitget GetClaw Hackathon. It merges multi-timeframe analysis, confluence scoring, regime detection, order-flow microstructure, and risk-first logic into a disciplined framework -- all controllable through a Telegram bot interface.

The system operates in **simulation-first mode by default**. Every trade idea must pass sixteen independent risk checks and receive explicit human confirmation before execution. An additional liquidity guard runs on live order-flow data when available. No exceptions.

**Key philosophy:** The bot suggests. The human decides. The risk engine enforces.

---

## Architecture

```
 Telegram Bot                       Bitget Exchange
      |                                   |
      v                                   v
 +-----------+    +---------------+   +-----------+
 |  Skill    |--->|  RuneClaw     |-->|  Market   |
 |  Registry |   |  Engine       |   |  Scanner  |
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

**Pipeline:** SCAN --> ANALYZE --> RISK GATE --> HUMAN CONFIRM --> EXECUTE (paper)

---

## Features

### Market Intelligence
- Real-time scanning of all Bitget USDT pairs
- Volume spike detection (2x rolling average)
- Momentum scoring with configurable thresholds
- Top N mover ranking with structured signal output

### AI Analysis Engine
- Technical indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR-14, ADX-14, VWAP, SMA-50 trend alignment, On-Balance Volume (OBV), Rolling VWAP (20-bar and 50-bar)
- Candlestick pattern detection: 14 patterns including doji, hammer, shooting star, engulfing, harami, tweezer top/bottom, morning/evening star, three white soldiers, three black crows
- Fibonacci retracement levels: swing high/low detection over 50-bar lookback, standard levels (23.6%, 38.2%, 50%, 61.8%, 78.6%) with zone classification
- 10-voter confluence scoring model (expanded from 6): RSI, MACD, Bollinger %B, Volume Spike, ADX, VWAP, OBV trend, candlestick pattern, Fibonacci zone, plus LLM confidence
- LLM-powered directional thesis generation (GPT-4o or compatible)
- Rule-based fallback when no LLM key is configured
- Structured `TradeIdea` output with entry, SL, TP, confidence, reasoning

### Risk Engine (Fail-Closed)
- **16 independent pre-trade checks** -- ALL must pass (plus a 17th liquidity guard on live order-flow data, fail-open)
- Circuit breaker halts trading on daily loss or drawdown breach
- Fixed-fractional position sizing: risk budget (2% of equity) divided by stop distance, capped at 20% notional
- Max open positions limit
- Risk/reward ratio minimum (1.2x)
- Confidence threshold gate (≥60%)
- Per-symbol exposure limit (20% max per asset)
- Correlation group concentration guard
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
| `/scan` | Scan market for top movers and volume spikes |
| `/analyze BTC` | Run AI analysis on a specific asset |
| `/portfolio` | View paper portfolio summary |
| `/trade` | View and confirm/reject pending trades |
| `/risk` | Risk metrics and circuit breaker status |
| `/rejected` | Recent risk-rejected trades with failure reasons |
| `/backtest` | Run backtest with synthetic data |
| `/status` | Bot mode, equity, open positions |
| `/halt` | Emergency kill-switch (trip breaker, cancel all) |
| `/help` | List all available commands |

Trade confirmation uses Telegram inline keyboards -- tap **Confirm** or **Reject** directly in the chat.

---

## Project Structure

```
runeclaw/
|-- bot/
|   |-- main.py                 # Entry point (telegram / cli / scan)
|   |-- config.py               # All settings from env, fail-closed defaults
|   |-- core/
|   |   |-- engine.py           # Central orchestrator (SCAN->ANALYZE->TRADE->MONITOR)
|   |   |-- market_scanner.py   # Bitget market scanner, volume spike detection
|   |   |-- analyzer.py         # AI + technical analysis, LLM thesis generation
|   |   |-- order_flow.py       # Exchange microstructure: CVD, book imbalance, whale detection
|   |-- risk/
|   |   |-- risk_engine.py      # 16-check risk gate, circuit breaker
|   |   |-- portfolio.py        # Paper trading ledger, PnL tracking, mark-to-market
|   |-- skills/
|   |   |-- skill_registry.py   # Modular skill system, built-in skills
|   |   |-- telegram_handler.py # Telegram bot commands, inline keyboards
|   |-- utils/
|   |   |-- models.py           # Pydantic schemas (TradeIdea, RiskCheck, etc.)
|   |   |-- logger.py           # Structured JSON audit logging
|   |-- requirements.txt
|-- docs/
|   |-- gitbook/                # Full GitBook documentation
|-- demo/
|   |-- sample_output.json      # Example trade idea
|   |-- sample_risk_check.json  # Example risk check
|   |-- sample_portfolio.json   # Example portfolio state
|-- scripts/
|   |-- demo_script.md          # 3-minute hackathon demo script
|-- .env.example
|-- LICENSE
|-- README.md
```

---

## Safety and Risk

RUNECLAW is designed with a **fail-closed** philosophy:

- **Simulation by default.** Live trading requires two explicit environment flags.
- **Every trade passes 16 checks.** One failure = rejection. No overrides.
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
| AI / LLM | OpenAI GPT-4o (configurable) |
| Technical Analysis | NumPy + custom indicators |
| Data Models | Pydantic v2 (strict, immutable) |
| Bot Interface | python-telegram-bot 20.x |
| Logging | Structured JSON (JSONL) |
| Config | python-dotenv + dataclass defaults |

---

## Team

| Role | Name |
|------|------|
| Lead Developer | *P.Baur* |
| AI / Strategy | *Claude + MuleRun + RUNECLAW* |
| Risk / Backend | *OPEN POSITION* |

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

---

<p align="center"><b>RUNECLAW</b> -- Where Viking grit meets algorithmic precision.</p>
