# RUNECLAW -- 3-Minute Hackathon Demo Script

## Timing: 3 minutes total

---

## [0:00 - 0:20] Opening Hook

> "What if your trading bot refused to lose more than 5% in a day -- and there was no override button?"

Pause. Let it land.

> "That is RUNECLAW -- an AI trading command core where the risk engine has veto power over the AI, and every trade requires human confirmation."

---

## [0:20 - 0:50] Problem Statement

> "Crypto trading bots have a trust problem. They either run fully autonomous with no guardrails, or they are so manual they defeat the purpose."

> "Traders need three things: intelligence to find opportunities, discipline to manage risk, and transparency to understand what happened. Most bots give you the first and ignore the rest."

---

## [0:50 - 2:00] Live Demo

### Show the Telegram bot

1. **Send `/scan`** -- Show real-time market scan results. Point out volume spikes and momentum scores.

> "RUNECLAW scans every USDT pair on Bitget, detects volume anomalies, and ranks by momentum."

2. **Send `/analyze BTC`** -- Show AI analysis output with entry, SL, TP, confidence, reasoning.

> "The analyzer combines RSI, MACD, Bollinger Bands with an LLM reasoning step. It produces a structured trade thesis -- not just a buy/sell signal."

3. **Point out the Confirm/Reject buttons.**

> "Notice: no trade executes automatically. The human decides."

4. **Tap Confirm.** Show the execution message.

> "The risk engine re-checks everything at confirmation time. If the market moved, it can still reject."

5. **Send `/portfolio`** -- Show the updated portfolio.

> "Full paper trading ledger. Balance, equity, win rate, PnL -- all tracked."

6. **Send `/risk`** -- Show risk dashboard.

> "Seven independent risk checks. Circuit breaker auto-halts on loss limits. No overrides."

---

## [2:00 - 2:30] Key Differentiators

> "Three things make RUNECLAW different:"

1. **"Fail-closed risk engine."** Every trade passes seven checks. One failure kills the trade. The circuit breaker cannot be bypassed.

2. **"Human-in-the-loop."** AI suggests, human decides. Re-check on confirmation catches market drift.

3. **"Full audit trail."** Every decision -- scans, analyses, confirmations, rejections -- is logged as structured JSON. Post-mortem is trivial.

---

## [2:30 - 3:00] Close

> "RUNECLAW is built on Bitget via ccxt, uses GPT-4o for analysis, and runs entirely through Telegram. Paper trading by default -- live trading requires two explicit flags."

> "We built this because we believe the best trading bot is one that knows when NOT to trade."

> "RUNECLAW -- where Viking grit meets algorithmic precision. Thank you."

---

## Tips for Presenters

- Have the Telegram bot running before the demo starts.
- Pre-populate one or two pending trades so the confirm flow is instant.
- If the API is slow, have screenshots ready as backup.
- Emphasize the risk engine and human confirmation -- these are the differentiators judges will remember.
- Keep the energy confident but not rushed. Let the product speak.
