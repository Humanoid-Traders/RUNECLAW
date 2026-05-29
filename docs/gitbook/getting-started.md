# Getting Started

This guide walks you through setting up RUNECLAW from scratch.

## Prerequisites

- Python 3.11 or newer
- A Bitget account (for API credentials -- sandbox mode is supported)
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- Optional: an OpenAI API key for LLM-powered analysis

## Installation

```bash
# Clone the repository
git clone https://github.com/Humanoid-Traders/RUNECLAW-AI-TRADER.git
cd RUNECLAW-AI-TRADER

# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install dependencies
pip install -r bot/requirements.txt
```

## Configuration

Copy the example environment file and fill in your credentials:

```bash
cp .env.example .env
```

Open `.env` in your editor and set the required values:

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | For Telegram mode | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Optional | Restrict to a specific chat |
| `BITGET_API_KEY` | For live data | Bitget API key |
| `BITGET_API_SECRET` | For live data | Bitget API secret |
| `BITGET_PASSPHRASE` | For live data | Bitget passphrase |
| `LLM_API_KEY` | Optional | OpenAI-compatible API key |
| `LLM_MODEL` | Optional | Model name (default: `gpt-4o`) |

All other settings have safe defaults. See `.env.example` for the full list.

## Running

RUNECLAW supports three modes:

### CLI Mode (default)

Interactive testing without Telegram. No bot token needed.

```bash
python -m bot.main --mode cli
```

You will see a `runeclaw>` prompt. Type a skill name to run it:

```
runeclaw> scan_market
runeclaw> analyze_asset BTC
runeclaw> get_portfolio
runeclaw> quit
```

### Telegram Mode

Start the full Telegram bot:

```bash
python -m bot.main --mode telegram
```

The bot will poll for updates. Open your Telegram bot and send `/help` to see available commands.

### Scan Mode

One-shot market scan that prints results and exits:

```bash
python -m bot.main --mode scan
```

## Verifying the Setup

1. Run `python -m bot.main --mode cli` and type `get_portfolio`.
2. You should see the default paper balance of $10,000.
3. Type `check_risk` to verify the risk engine reports all-clear.
4. Type `quit` to exit.

If you see the RUNECLAW banner and portfolio output, the system is ready.

## Next Steps

- Read the [Architecture](architecture.md) overview to understand how the pieces fit together.
- Explore [Skills & Commands](skills-and-commands.md) for the full command reference.
- Review the [Risk Framework](risk-framework.md) before generating trade ideas.
