# RUNECLAW — Submission for Bitget AI Base Camp · Hackathon S1

**Track:** Best Strategy & Risk Award
**Team:** Humanoid Traders
**Live Bot:** [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)
**Dashboard:** [Live Dashboard](https://xbf5gmte.mule.page/)
**Repo:** [GitHub](https://github.com/Humanoid-Traders/RUNECLAW)

---

## What RUNECLAW Does (30 seconds)

RUNECLAW is an AI trading agent that fuses 11-voter confluence scoring with a 21-check fail-closed risk engine, human-in-the-loop Telegram confirmation, live Bitget execution via ccxt, and an Ed25519-signed tamper-evident audit chain -- every decision is logged, gated, and cryptographically attested before a single dollar moves.

## Why We Win Best Strategy & Risk

### Strategy
- **11-voter confluence scoring** -- RSI, MACD, Bollinger, Volume, ADX, VWAP, OBV, candlestick patterns (14 types), Fibonacci zones, sentiment fusion, plus LLM confidence (60/40 rule/AI blend)
- **Multi-timeframe regime detection** -- ADX-14 classifies TREND_UP/DOWN/RANGE/CHOP; EMA20/50 alignment across 1H/4H/1D with BOS/CHoCH structural signals
- **Smart money microstructure** -- liquidation cascade detection, funding-rate squeeze, whale flow tracking with stealth accumulation, composite score normalized [-1, 1]
- **Self-improving learning loop** -- 8-module system (experience memory, reflection engine, strategy evaluator with S/A/B/C/D tiers, pattern learner, macro learner, model comparer, prompt optimizer, feedback collector); safety policy enforces immutable risk boundaries
- **5 adaptive strategy modes** -- Trend Continuation, Breakout, Mean Reversion, Liquidity Sweep, Conservative; each with per-mode SL/TP multipliers and confidence thresholds
- **40+ analysis modules** spanning technical indicators, candlestick detection, Fibonacci retracement, order flow, sentiment fusion, macro calendar, and explainability engine

### Risk
- **21 independent fail-closed risk checks** -- position size, daily loss, drawdown, max positions, R:R minimum, confidence gate, correlation blocking, loss streak, entry sanity, stop-loss required, stale data guard, cooldown, portfolio exposure, per-symbol exposure, volatility guard, circuit breaker, liquidity guard, macro event gate, MTF alignment, concentration PCA, portfolio VaR
- **Adversarial self-critique gate** -- 7-heuristic bear-case analysis pre-trade; HALT at 3+ concerns blocks execution with full explanation
- **Ed25519 cryptographic attestation** -- Merkle root over audit entry batches, signed with Ed25519; verify any batch against public key for non-repudiation
- **Portfolio VaR check (#21)** -- 95% parametric VaR rejects trades pushing portfolio risk above 15% of equity
- **Compliance engine** -- explainability scoring, data sufficiency checks, risk documentation, MiCA-aligned audit trail with factor attribution
- **Circuit breaker + black swan detector** -- auto-halt on 5% daily loss / 10% drawdown / 5 consecutive losses; black swan detector pre-empts with 5 statistical anomaly types (correlation breakdown, volume collapse, flash crash, ATR explosion, spread widening)

### Live Proof
- **Real BTC/USDT execution on Bitget** -- live bot [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot) scanning 324 pairs, analyzing, and gating trades in production
- **Micro-test safety limits** -- simulation-first ($10K paper), live requires dual-flag opt-in; configurable $10/position, $50 total exposure caps
- **551 automated tests, 100% red team pass rate** -- 28 adversarial scenarios across 10 attack categories, 29 dedicated security tests, zero crashes across 500 backtest runs

### Composability (MCP)
- **Shield risk engine available as MCP server** -- bearer-token authenticated, any external agent calls `check_risk()` over MCP protocol (`bot/mcp/server.py`)
- **5-agent swarm protocol** -- Scanner/Analyst/Risk/Executor/Sentinel communicate via SwarmBus pub/sub; Sentinel broadcasts HALT on severity >= 0.8

## Architecture (One Paragraph)

RUNECLAW operates as a 9-state FSM: **SCAN** detects volume anomalies across all Bitget USDT pairs, **ANALYZE** runs 11-voter confluence scoring with regime detection and smart money signals, **RISK GATE** enforces 21 fail-closed checks plus adversarial self-critique, **HUMAN CONFIRM** requires explicit Telegram approval with inline keyboard, **EXECUTE** places the order via ccxt with trailing stops at 1R profit. Every state transition, risk decision, and trade outcome is logged as structured JSONL and cryptographically attested. Circuit breaker and black swan detector can halt the pipeline at any point.

## Quick Start

```bash
git clone https://github.com/Humanoid-Traders/RUNECLAW.git
cd RUNECLAW && pip install -r bot/requirements.txt
cp .env.example .env  # Add your keys (or set LLM_PROVIDER=gemini for zero-cost)
python -m bot.main --mode telegram
```

## Contact

**Team:** Patrick Baur (Lead Developer) · Daan (Co-Founder & Strategy)
**Telegram Community:** [Join](https://t.me/+VRNgsmkR5pszZTdk) · **X:** [@BaurPatric70363](https://x.com/BaurPatric70363)
**GitBook Docs:** [humanoid-traders-1.gitbook.io](https://humanoid-traders-1.gitbook.io/humanoid-traders-ai)
