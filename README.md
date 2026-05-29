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
  <a href="https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER">GitHub</a> &middot;
  <a href="https://humanoid-traders-1.gitbook.io/humanoid-traders-ai">Documentation</a> &middot;
  <a href="https://t.me/+VRNgsmkR5pszZTdk">Telegram</a>
</p>

---

## What is RUNECLAW?

**RUNECLAW** is an elite AI trading command system built by **Humanoid Traders** for the Bitget GetClaw Hackathon. It merges multi-timeframe analysis, confluence scoring, regime detection, macro awareness, and risk-first logic into a battle-forged framework -- all controllable through a Telegram bot interface.

The system operates in **simulation-first mode by default**. Every trade idea must pass fifteen independent risk checks and receive explicit human confirmation before execution. No exceptions.

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
- Technical indicators: RSI-14, MACD (12/26/9), Bollinger Bands (20/2), ATR
- LLM-powered directional thesis generation (GPT-4o or compatible)
- Rule-based fallback when no LLM key is configured
- Structured `TradeIdea` output with entry, SL, TP, confidence, reasoning

### Risk Engine (Fail-Closed)
- **15 independent pre-trade checks** -- ALL must pass
- Circuit breaker halts trading on daily loss or drawdown breach
- Position sizing capped at configurable % of equity
- Max open positions limit
- Risk/reward ratio minimum (1.5x)
- Confidence threshold gate (>50%)
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
git clone https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER.git
cd runeclaw

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
| `/status` | Bot mode, equity, open positions |
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
|   |-- risk/
|   |   |-- risk_engine.py      # 15-check risk gate, circuit breaker
|   |   |-- portfolio.py        # Paper trading ledger, PnL tracking
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
- **Every trade passes 15 checks.** One failure = rejection. No overrides.
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
| Lead Developer | *Your Name* |
| AI / Strategy | *Team Member* |
| Risk / Backend | *Team Member* |

---

## License

MIT License. See [LICENSE](./LICENSE) for details.

---

<p align="center"><b>RUNECLAW</b> -- Where Viking grit meets algorithmic precision.</p>
