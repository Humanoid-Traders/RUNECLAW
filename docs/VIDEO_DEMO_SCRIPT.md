# RUNECLAW Video Demo Script

## Bitget AI Base Camp · Hackathon S1

**Duration:** 2:50-3:00
**Tone:** Confident, direct, hackathon energy. Professional but not corporate.

---

## [0:00-0:15] Opening -- The Hook

**[SCREEN]** RUNECLAW ASCII art logo fading in, then a quick montage: risk check terminal output scrolling, a red "REJECTED" flash, then a green "CONFIRMED" with the Telegram keyboard visible.

**[VOICEOVER]** "RUNECLAW is the only AI trading agent that can't blow up your account. Not because it's smarter than the market -- because it's designed to say no. Eighteen mandatory risk gates. Human confirmation on every trade. Fail-closed by default. Let me show you how it works."

---

## [0:15-0:45] What It Is -- Pipeline Overview

**[SCREEN]** Architecture diagram from the README: the pipeline flow from Telegram Bot through Engine, Scanner, Analyzer, Risk Engine, Human Confirmation, to Portfolio Tracker. Highlight each component as it is mentioned.

**[VOICEOVER]** "RUNECLAW is a 9-state finite state machine that governs every trade from scan to cooldown. The pipeline goes: market scan, AI analysis with a 10-voter confluence model, then an 20-check fail-closed risk gate. If the trade survives all of that, it goes to you -- the human -- for confirmation via Telegram. Only then does it execute, in paper trading mode, with a ten thousand dollar virtual balance. There is no autonomous execution. There is no override."

**[SCREEN]** Brief flash of the FSM state diagram: IDLE -> SCANNING -> ANALYZING -> RISK_CHECK -> CONFIRMING -> EXECUTING -> MONITORING -> COOLING_DOWN / HALTED.

---

## [0:45-1:15] Live Demo: /scan + /analyze

**[SCREEN]** Telegram chat window. User types `/scan`. Bot responds with a formatted list of top movers -- ticker symbols, volume spike ratios, momentum scores. Highlight one result (e.g., BTCUSDT showing a 2.3x volume spike).

**[VOICEOVER]** "Here's a live market scan. RUNECLAW checks all Bitget USDT pairs for volume anomalies -- anything above two-x the rolling average gets flagged. We can see BTC showing a significant volume spike. Let's dig deeper."

**[SCREEN]** User types `/analyze BTC`. Bot responds with a detailed analysis card: RSI value, MACD signal, Bollinger Band position, ADX regime classification (e.g., "TREND_UP"), confidence score, and a structured trade idea with entry, stop-loss, take-profit, and reasoning.

**[VOICEOVER]** "The analysis engine polls ten independent voters -- RSI, MACD, Bollinger Bands, volume, ADX, VWAP, OBV, candlestick patterns, Fibonacci levels, and an LLM thesis. It detects the current regime -- in this case, a trend-up environment -- and adjusts confidence accordingly. The result is a structured trade idea with entry, stop, target, and a confidence score."

---

## [1:15-1:45] Risk Gate Demo -- The Rejection

**[SCREEN]** A trade idea card appears with a moderate confidence score (e.g., 58%). The risk engine output displays below it: a list of 20 checks, most showing green checkmarks, but one highlighted in red -- "CONFIDENCE_THRESHOLD: FAILED (58% < 60% minimum)". Final verdict: large red banner reading "REJECTED".

**[VOICEOVER]** "This is where RUNECLAW is different. This trade idea scored fifty-eight percent confidence -- just below the sixty percent threshold. Watch what happens. Eighteen checks run. Seventeen pass. One fails. Result: rejected. Not 'proceed with caution.' Not 'override available.' Rejected. Period. In a fail-closed system, one failure out of eighteen is enough. The risk engine does not negotiate."

**[SCREEN]** Quick scroll through the rejection log entry in JSONL format, showing the timestamp, the failing check name, the threshold, and the actual value.

**[VOICEOVER]** "Every rejection is logged with full context -- which check failed, what the threshold was, what the actual value was. Complete audit trail. No black boxes."

---

## [1:45-2:15] Confirmation Flow -- Human in the Loop

**[SCREEN]** A new trade idea appears with a passing confidence score (e.g., 72%). All 18 risk checks show green. A Telegram inline keyboard appears with two buttons: "Confirm" and "Reject".

**[VOICEOVER]** "Now here's a trade that passes all eighteen checks. Seventy-two percent confidence. Good risk-reward ratio. Volatility within bounds. But it does not execute yet. It waits for you."

**[SCREEN]** User taps "Confirm". A brief loading indicator, then a second risk check result appears -- "Re-check passed." Followed by the execution confirmation: paper trade placed, position details, stop-loss and take-profit levels.

**[VOICEOVER]** "When you tap confirm, the risk engine runs all eighteen checks again against current market conditions. Markets move. What was safe thirty seconds ago might not be safe now. This second pass catches drift. Once re-check passes, the paper trade executes. Entry, stop-loss, take-profit -- all logged, all tracked, all persistent. If the bot restarts, your positions survive."

---

## [2:15-2:30] Backtest Results -- The Evidence

**[SCREEN]** Clean data card or slide showing key metrics in large text:
- 500 backtest runs
- 889 trades
- 50.5% win rate
- Worst drawdown: 3.87%
- Zero crashes
- Trailing stops: 48.7% of exits

Small disclaimer text at bottom: "Synthetic data -- not historical. Not a guarantee of future performance."

**[VOICEOVER]** "Five hundred backtest runs across five market regimes, twenty symbols, and five seeds. Eight hundred eighty-nine trades. Worst drawdown: three point eight seven percent. Zero crashed runs. Best run: plus eight point zero six percent. Trailing stops handled nearly half of all exits with net-positive PnL. And yes -- this is synthetic data. We're honest about that. These numbers prove the risk architecture works under stress. They don't promise alpha."

---

## [2:30-2:50] Agent Hub Integration -- MCP Tools

**[SCREEN]** Diagram showing the 8 MCP tools exposed by RUNECLAW: `scan_market`, `analyze_asset`, `get_portfolio`, `get_risk_status`, `confirm_trade`, `run_backtest`, `get_rejected`, `emergency_halt`. Arrows showing connections to the GetClaw Agent Hub ecosystem.

**[VOICEOVER]** "RUNECLAW is not just a standalone bot. It exposes eight tools via Model Context Protocol -- the same standard the GetClaw Agent Hub uses. Any MCP-compatible agent can scan markets through RUNECLAW, request analysis, check risk status, or trigger an emergency halt. It plugs directly into the Agent Hub ecosystem as a risk-managed trading primitive that other agents can build on top of."

---

## [2:50-3:00] Closing -- The Tagline

**[SCREEN]** Black screen. Text fades in, line by line:

> **RUNECLAW**
> AI proposes. Humans decide. The risk engine enforces.

Then links appear below:
- GitHub: github.com/Humanoid-Traders/RUNECLAW
- Website: lgl3crf9.mule.page
- Telegram: t.me/+VRNgsmkR5pszZTdk

**[VOICEOVER]** "RUNECLAW. AI proposes. Humans decide. The risk engine enforces. Links in the description. AGPL-3.0 licensed. Fully open source. Built for Bitget AI Base Camp."

---

## Production Notes

- **Total runtime target:** 2:50-3:00
- **Screen recording tool:** OBS or similar, 1080p minimum
- **Telegram demo:** Use a live bot instance in paper trading mode against Bitget testnet or mainnet (read-only API key)
- **For the rejection demo:** Either find a naturally low-confidence setup or temporarily raise the confidence threshold to 70% to guarantee a rejection occurs during recording
- **Music:** Optional -- low ambient electronic, no lyrics, mix at 10-15% volume under voiceover
- **Pacing:** Each section should feel punchy. Do not linger. Cut between screen recordings and clean data slides. No filler slides with bullet points -- show the actual system working.

---

#BitgetAIAgent #GenesisS1
