# RUNECLAW

**AI Trading Command Core | Forged in Volatility. Governed by Discipline.**

*by Humanoid Traders | for Bitget GetClaw*

Welcome to the RUNECLAW documentation. This guide covers everything from initial setup to the internal architecture of the system.

## Overview

RUNECLAW is an AI-powered trading assistant that scans the Bitget exchange for opportunities, generates trade theses using LLM analysis and technical indicators, enforces risk limits through a fail-closed gate, and requires human confirmation before every trade.

The system operates in **paper trading mode by default** and is designed for the Bitget GetClaw Hackathon.

## Core Principles

1. **Simulation-first.** Live trading is disabled unless explicitly enabled with two environment flags.
2. **Fail-closed risk.** Every trade must pass all 18 pre-trade checks. One failure means rejection.
3. **Human-in-the-loop.** No trade executes without explicit confirmation via Telegram inline keyboard.
4. **Full auditability.** Every decision is logged as structured JSON for post-mortem review.

## Documentation Sections

- [Getting Started](getting-started.md) -- Setup and first run
- [Architecture](architecture.md) -- System design and data flow
- [Skills & Commands](skills-and-commands.md) -- Telegram commands and the skill system
- [Risk Framework](risk-framework.md) -- How risk is managed and enforced
- [Paper Trading](paper-trading.md) -- The paper trading ledger
- [API Reference](api-reference.md) -- Data models and programmatic interface
- [FAQ](faq.md) -- Common questions

## Quick Links

- **GitHub:** [RUNECLAW](https://github.com/Humanoid-Traders/RUNECLAW)
- **Website:** [lgl3crf9.mule.page](https://lgl3crf9.mule.page/)
- **Telegram:** [Join Community](https://t.me/+VRNgsmkR5pszZTdk)
- **X / Twitter:** [@BaurPatric70363](https://x.com/BaurPatric70363)
- **Hackathon:** Bitget GetClaw 2025
- **License:** MIT

> **Disclaimer:** RUNECLAW is an educational hackathon prototype. It is not production-ready and should not be used with real funds without extensive additional safeguards, independent audits, and regulatory review. Backtest results use synthetic data and do not predict future performance. This is not financial advice.
