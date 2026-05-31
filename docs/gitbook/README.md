# Introduction

**AI Trading Command Core | Forged in Volatility. Governed by Discipline.**

*by Humanoid Traders | built for Bitget AI Base Camp / Agent Hub*

Welcome to the official RUNECLAW documentation.

RUNECLAW is a risk-first AI trading assistant designed for the **Bitget AI Base Camp · Hackathon S1**. It scans Bitget markets for opportunities, generates trade theses using LLM analysis and technical indicators, applies macro and volatility context, enforces strict risk controls, and requires human confirmation before any trade can proceed.

The system runs in **paper trading mode by default** and is built around safety, transparency, explainability, and disciplined execution.

> **RUNECLAW is not another hype bot. It is a survival-first AI trading agent.**

---

## Overview

RUNECLAW supports the full trading decision loop:

```text
Market Data
   ↓
Technical Indicators
   ↓
LLM Trade Thesis
   ↓
Macro / Volatility / Risk Context
   ↓
Fail-Closed Risk Gate
   ↓
Human Confirmation
   ↓
Paper Trade / Future Bitget Execution
   ↓
Structured Audit Log
```

---

## Core Principles

1. **Simulation-first.** Live trading is disabled unless explicitly enabled with two environment flags.
2. **Fail-closed risk.** Every trade must pass all 18 pre-trade checks (17 fail-closed + 1 fail-open for liquidity only). One failure means rejection.
3. **Human-in-the-loop.** No trade executes without explicit confirmation via Telegram inline keyboard.
4. **Full auditability.** Every decision is logged as structured JSON for post-mortem review.

---

## Documentation Sections

- [Getting Started](getting-started.md) -- Setup and first run
- [Architecture](architecture.md) -- System design and data flow
- [Skills & Commands](skills-and-commands.md) -- Telegram commands and the skill system
- [Risk Framework](risk-framework.md) -- How risk is managed and enforced
- [Paper Trading](paper-trading.md) -- The paper trading ledger
- [API Reference](api-reference.md) -- Data models and programmatic interface
- [FAQ](faq.md) -- Common questions

---

## Quick Links

- **Try the Bot:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot) -- live on Telegram, try it now
- **GitHub:** [RUNECLAW](https://github.com/Humanoid-Traders/RUNECLAW)
- **Website:** [xbf5gmte.mule.page](https://xbf5gmte.mule.page/)
- **Telegram:** [Join Community](https://t.me/+VRNgsmkR5pszZTdk)
- **X / Twitter:** [@BaurPatric70363](https://x.com/BaurPatric70363)
- **Team:** Patrick Baur (Lead Developer) & Daan (Co-Founder & Strategy)
- **Hackathon:** Bitget AI Base Camp · Hackathon S1
- **License:** AGPL-3.0

> **Disclaimer:** RUNECLAW is an educational hackathon prototype. It is not production-ready and should not be used with real funds without extensive additional safeguards, independent audits, and regulatory review. Backtest results use synthetic data and do not predict future performance. This is not financial advice.
