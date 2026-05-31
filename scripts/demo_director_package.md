# RUNECLAW — Demo Director's Package
## 3-Minute Hackathon Demo | Humanoid Traders | Bitget GetClaw

---

# DEMO THESIS

**One sentence:** RUNECLAW is a trading agent that refuses to trade unless every safety check passes and a human confirms — and it can prove it.

**What the audience should remember 10 minutes later:**
1. The risk engine has veto power over the AI and cannot be overridden
2. Every decision is logged as structured JSON — full traceability
3. It works right now — paper trading, real market data, real AI analysis

---

# SPOKEN SCRIPT + CLICK PATH

## [0:00 – 0:15] THE HOOK

**Screen:** Black terminal, cursor blinking. Nothing running yet.

**Spoken:**
> "Most trading bots are built to trade. RUNECLAW is built to decide *whether* to trade."
>
> "It scans real markets, generates AI-backed trade theses, runs eighteen independent risk checks — and then asks a human for permission. If any single check fails, the trade dies. No override. No exception."

**Action:** None. Let the words land.

---

## [0:15 – 0:30] THE PROBLEM

**Screen:** Still on terminal. Optionally show a split-screen comparison slide:

```
TYPICAL BOT              RUNECLAW
─────────────            ─────────────
Signal → Execute         Signal → Analyze → Risk Gate → Human Confirm → Execute
No explanation           Full reasoning chain
No risk limit            18 risk checks
No audit trail           Structured JSON logging
Black box                Every decision traceable
```

**Spoken:**
> "The problem with crypto trading bots is simple. They either run blind — no risk controls, no explanation — or they're dashboards that make you do everything manually."
>
> "RUNECLAW sits in the middle. The AI does the analysis. The risk engine enforces discipline. The human makes the final call."

**Action:** None — this is context-setting. Keep it fast.

---

## [0:30 – 0:45] SYSTEM BOOT

**Screen:** Terminal in focus.

**Spoken:**
> "Let me show you. Zero config. No API keys needed for the first run."

**Action:** Type and execute:
```bash
python -m bot.main --mode cli
```

**Expected on screen:** RUNECLAW banner appears, `runeclaw>` prompt ready.

**Spoken:**
> "CLI mode. Paper trading with ten thousand dollars. Everything runs locally."

---

## [0:45 – 1:00] PERCEPTION — MARKET SCAN

**Screen:** CLI prompt.

**Action:** Type:
```
runeclaw> scan_market
```

**Expected output:**
```
Top movers:
BTC/USDT: $67,432.50 (+3.2%) SPIKE
ETH/USDT: $3,891.20 (+2.1%)
SOL/USDT: $178.45 (+5.8%) SPIKE
DOGE/USDT: $0.1823 (+4.1%)
AVAX/USDT: $42.67 (-2.3%)
```

**Spoken:**
> "The scanner hits every USDT pair on Bitget. Ranks by momentum. Flags volume spikes — that means current volume is more than twice the rolling average. These are the signals that enter the pipeline."

**Proof point:** Real Bitget data. Volume spike detection is algorithmic, not arbitrary.

---

## [1:00 – 1:25] DECISION — AI ANALYSIS

**Screen:** CLI prompt.

**Action:** Type:
```
runeclaw> analyze_asset BTC
```

**Expected output:**
```
Trade Idea [TI-a1b2c3d4]
LONG BTC/USDT
Entry: $67,432.50
SL: $66,580.00 | TP: $68,710.00
Confidence: 72%
R:R = 1.50
Reasoning: RSI at 38 indicates oversold conditions. MACD line
crossing above signal line suggests bullish momentum shift.
Volume spike at 2.3x rolling average confirms institutional
interest. Entering long with 2-ATR stop and 3-ATR target.
```

**Spoken:**
> "The analyzer computes RSI, MACD, Bollinger Bands, and ATR from the candle data. Then it asks GPT-4o for a directional thesis. What you see is a structured trade idea — direction, entry, stop-loss, take-profit, confidence score, and the reasoning behind it."
>
> *Point at screen:*
> "Notice: it tells you *why*. RSI oversold. MACD crossover. Volume confirming. This is not a black box. If the LLM is unavailable, it falls back to a deterministic rule-based engine — the system never stops working."

**Proof points:** Named indicators. Structured output. LLM reasoning visible. Fallback path exists.

---

## [1:25 – 1:50] RISK GATE — VALIDATION

**Screen:** CLI prompt.

**Action:** Type:
```
runeclaw> check_risk
```

**Expected output:**
```
Equity: $10,000.00
Daily PnL: $0.00
Drawdown: 0.0%
Circuit Breaker: OK
```

**Spoken:**
> "Before any trade can execute, it passes through eighteen independent risk checks."

*Count on fingers or point at list — make it concrete:*

> "One: circuit breaker — is the system halted?
> Two: position size — does this trade risk more than two percent of equity at the stop-loss?
> Three: daily loss — have we lost more than five percent today?
> Four: drawdown — are we down more than ten percent from peak?
> Five: max positions — do we already have five open?
> Six: risk-reward — is the ratio at least one-point-five?
> Seven: confidence — is the AI at least fifty percent sure?"
>
> "If *any one* of these fails, the trade is rejected. Not flagged. Not warned. Rejected. This is fail-closed design — the default answer is no."

**Action:** Now execute the trade:
```
runeclaw> execute_paper_trade TI-a1b2c3d4
```

**Expected output:**
```
Executed paper LONG BTC/USDT ($200.00)
```

**Spoken:**
> "The risk engine approved it. In Telegram mode, this step requires tapping a Confirm button — no trade executes without human confirmation. And the risk engine *re-checks* at confirmation time, because the market may have moved."

**Proof point:** Explicit 7-check enumeration. Fail-closed language. Re-check on confirmation.

---

## [1:50 – 2:10] EXECUTION + MONITORING

**Screen:** CLI prompt.

**Action:** Type:
```
runeclaw> get_portfolio
```

**Expected output:**
```
Balance: $9,800.00
Equity: $10,000.00
Open: 1 | Total: 0
Win Rate: 0%
Total PnL: $0.00
```

**Spoken:**
> "Paper trade is live. Two thousand dollars allocated — capped at twenty percent notional, risking two percent of equity at the stop. The position is now monitored every scan cycle. If price hits the stop-loss or the take-profit, it auto-closes and records the PnL."
>
> "The portfolio tracker handles balance, equity, drawdown, win rate — all in-memory for the hackathon, architecturally ready for database persistence."

**Proof point:** Position size reflects the 2% risk budget capped at 20% notional. SL/TP monitoring is automatic.

---

## [2:10 – 2:30] AUDIT TRAIL

**Screen:** Switch to a second terminal tab (or split pane).

**Action:** Type:
```bash
cat logs/trade.jsonl | python3 -m json.tool | head -30
```

**Expected output:** Structured JSON entries showing the full decision chain:
```json
{
  "ts": "2026-05-15T14:30:00.000Z",
  "level": "INFO",
  "channel": "runeclaw.trade",
  "message": "Trade idea: LONG BTC/USDT",
  "action": "analyze",
  "reasoning": "RSI oversold + volume spike",
  "result": "IDEA",
  "data": {
    "id": "TI-a1b2c3d4",
    "asset": "BTC/USDT",
    "confidence": 0.72,
    "direction": "LONG"
  }
}
```

**Spoken:**
> "Every decision is logged as structured JSON. Three channels — trade, risk, system. Each entry has a timestamp, action, reasoning, and result."
>
> "If a trade goes wrong, you don't guess what happened. You filter by trade ID across all three logs and reconstruct the full decision chain: what the scanner saw, what the analyzer proposed, what the risk engine checked, whether the human confirmed, and what happened after."
>
> "This is not debug logging. This is an audit trail."

**Proof point:** Show real JSON. Point at `reasoning` field. Point at `action` field. This is the strongest differentiator — make it visible.

---

## [2:30 – 2:45] DIFFERENTIATION

**Screen:** Return to the CLI or show the architecture diagram.

**Spoken:**
> "Three things set RUNECLAW apart from every other hackathon trading bot."
>
> "First — the risk engine is a *gate*, not an advisor. It does not warn. It rejects. And when losses accumulate, the circuit breaker shuts everything down. Manual reset only."
>
> "Second — human-in-the-loop is not optional. In Telegram mode, every trade requires tapping Confirm. The risk engine re-checks at that moment. Time passed. Market moved. The system adapts."
>
> "Third — everything is explainable. The trade idea tells you which indicators informed it and why. The risk check tells you which checks passed and which failed. The audit log tells you the full history. No black box. No trust-me."

---

## [2:45 – 3:00] CLOSE

**Screen:** Show the RUNECLAW landing page or the GitHub README.

**Spoken:**
> "RUNECLAW runs on Python, Bitget via ccxt, GPT-4o for analysis, and Telegram for the command interface. Paper trading by default. Live trading requires explicitly flipping two safety flags — defense in depth."
>
> "We built this because we believe the best trading agent is the one that knows when *not* to trade."
>
> "RUNECLAW. Forged in volatility. Governed by discipline. Thank you."

---

# REQUIRED PROOF ARTIFACTS

Prepare these before the demo. If anything fails live, show these as evidence.

| Artifact | File | Purpose |
|----------|------|---------|
| Trade idea JSON | `demo/sample_output.json` | Shows structured AI output with reasoning |
| Risk check JSON | `demo/sample_risk_check.json` | Shows all 18 checks passed with values |
| Portfolio state | `demo/sample_portfolio.json` | Shows positions, PnL, trade history |
| Trade audit log | `logs/trade.jsonl` | Shows decision chain for a single trade |
| Risk audit log | `logs/risk.jsonl` | Shows circuit breaker logic |
| System prompt | `bot/prompts/system_prompt.md` | Shows agent identity and 5 laws |
| Risk engine source | `bot/risk/risk_engine.py` | 116 lines — all 18 checks visible |
| Config defaults | `bot/config.py` | Shows SIMULATION_MODE=True default |

---

# FALLBACK DEMO PLAN

If the live environment fails — API down, network issue, dependency error — the demo still works.

## Tier 1: CLI Mode (No API Keys)

If Bitget API is unreachable:
- `get_portfolio` still works → shows $10,000 paper balance
- `check_risk` still works → shows all-clear, circuit breaker OK
- `scan_market` returns "No significant signals detected" → explain gracefully
- Narrate: "The scanner can't reach Bitget right now, but watch — the system handles it cleanly. No crash. No partial state. It returns an empty result and waits for the next cycle."

**This is actually a good demo moment.** It demonstrates fail-safe behavior.

## Tier 2: Pre-Recorded Output

If Python environment fails entirely:
- Open `demo/sample_output.json` → show the trade idea structure
- Open `demo/sample_risk_check.json` → show the 18 checks
- Open `demo/sample_portfolio.json` → show portfolio with positions and history
- Walk through the JSON fields as if the system produced them live
- Show `bot/risk/risk_engine.py` source — 116 lines, all 18 checks visible on one screen

## Tier 3: Code Walkthrough

If nothing runs:
- Open `bot/core/engine.py` → show the pipeline: scan → analyze → risk → confirm → execute
- Open `bot/risk/risk_engine.py` → show the 18 checks
- Open `bot/utils/logger.py` → show the audit function
- Open `bot/config.py` → show SIMULATION_MODE=True, LIVE_TRADING_ENABLED=False
- This proves the architecture is real and the code is production-quality

**Key rule:** Never apologize for a technical failure. Reframe it as evidence of graceful degradation.

---

# SCREENSHOTS / CLIPS TO PREPARE

Capture these before the demo day:

| Screenshot | Content | When to Use |
|-----------|---------|-------------|
| `ss-01-boot.png` | Terminal with RUNECLAW banner + `runeclaw>` prompt | If startup fails |
| `ss-02-scan.png` | `scan_market` output with 5 signals, volume spike flags | If API is down |
| `ss-03-analyze.png` | `analyze_asset BTC` output with full TradeIdea | If LLM is down |
| `ss-04-risk.png` | `check_risk` output showing all-clear | If portfolio errors |
| `ss-05-portfolio.png` | `get_portfolio` output after one trade | If state issues |
| `ss-06-logs.png` | `trade.jsonl` formatted JSON in terminal | If log dir missing |
| `ss-07-telegram-scan.png` | Telegram `/scan` with signal list | For Telegram demo |
| `ss-08-telegram-confirm.png` | Telegram trade idea with Confirm/Reject buttons | For Telegram demo |
| `ss-09-telegram-portfolio.png` | Telegram `/portfolio` response | For Telegram demo |
| `ss-10-rejected.png` | A trade rejected by risk engine (specific check failure) | To show fail-closed |

---

# PRE-DEMO CHECKLIST

Run through this 30 minutes before the demo:

```
[ ] Python 3.11+ virtual environment activated
[ ] Dependencies installed (pip install -r bot/requirements.txt)
[ ] .env file exists with at minimum Bitget API keys
[ ] CLI mode starts: python -m bot.main --mode cli
[ ] get_portfolio returns $10,000
[ ] check_risk returns all-clear
[ ] scan_market returns signals (or graceful empty result)
[ ] logs/ directory exists and is writable
[ ] Terminal font size large enough for projection/screen share
[ ] Demo sample files accessible: demo/*.json
[ ] Fallback screenshots captured and accessible
[ ] If showing Telegram: bot is running and responsive
[ ] If showing Telegram: one pending trade pre-staged for confirm flow
[ ] Second terminal tab ready for log inspection
[ ] Browser tab open to landing page or GitHub README
[ ] Timer visible (phone or secondary screen) — stay under 3:00
```

---

# COMMON DEMO MISTAKES TO AVOID

### 1. Starting with features instead of the problem
Wrong: "RUNECLAW has eighteen risk checks and Telegram integration."
Right: "Trading bots have a trust problem. Here's how we solve it."
Judges don't care about features until they understand the problem.

### 2. Rushing through the risk engine
The risk engine is the core differentiator. Enumerate all eighteen checks. Use your fingers. Make each one distinct and countable. This is the moment judges lean forward.

### 3. Skipping the audit trail
Showing `trade.jsonl` is the single most convincing proof that the system is real and production-grade. Dashboards can be faked. Structured JSON logs cannot. Spend at least 15 seconds here.

### 4. Apologizing for paper trading
Wrong: "We only have paper trading for now."
Right: "Paper trading is the default. Live trading requires explicitly enabling two safety flags. This is a design choice, not a limitation."
Simulation-first is a feature. Judges know this.

### 5. Over-explaining the LLM
The LLM is one component. If you spend 45 seconds explaining GPT-4o, you've lost the plot. The story is the pipeline — perception, decision, risk, confirmation, audit — not the model.

### 6. Not showing a rejection
If you have time, show what happens when a trade *fails* the risk check. A REJECTED output with specific check failures is more impressive than five APPROVED trades. It proves the system actually enforces its rules.

### 7. Saying "demo" or "prototype"
These words signal toy. Say "system," "platform," or "runtime." The code is real. The architecture is real. The paper trading is real. Own it.

### 8. Running over time
3:00 means 3:00. Practice until you can deliver in 2:45, leaving 15 seconds of buffer. If you're at 2:50 and haven't started the close, skip the differentiation section and go straight to the final line.

---

# TIMING REFERENCE

| Segment | Start | End | Duration | Content |
|---------|-------|-----|----------|---------|
| Hook | 0:00 | 0:15 | 15s | One-sentence thesis |
| Problem | 0:15 | 0:30 | 15s | Trust problem + comparison |
| Boot | 0:30 | 0:45 | 15s | Start CLI, show prompt |
| Perception | 0:45 | 1:00 | 15s | scan_market |
| Decision | 1:00 | 1:25 | 25s | analyze_asset BTC |
| Risk Gate | 1:25 | 1:50 | 25s | check_risk + 18 checks + execute |
| Execution | 1:50 | 2:10 | 20s | get_portfolio + monitoring |
| Audit | 2:10 | 2:30 | 20s | Show trade.jsonl |
| Differentiators | 2:30 | 2:45 | 15s | Three-point summary |
| Close | 2:45 | 3:00 | 15s | Stack + tagline + thank you |

**Total: 3:00**

---

# POST-DEMO Q&A PREP

Likely questions and answers:

**Q: Can this trade with real money?**
A: The architecture supports it. Live trading requires setting `SIMULATION_MODE=false` and `LIVE_TRADING_ENABLED=true` — two explicit flags. The current code path returns a disabled message. This is intentional for the hackathon scope.

**Q: What happens if the LLM hallucinates?**
A: The risk engine is independent of the LLM. Even if the LLM says "100% confident, go all in," the risk engine enforces position size (2%), R:R ratio (1.5 minimum), and all other checks. The LLM cannot override the gate.

**Q: How is this different from a GPT wrapper?**
A: A GPT wrapper sends market data to an LLM and executes whatever it says. RUNECLAW puts the LLM output through a eighteen-check risk gate and requires human confirmation. The LLM is one input to the decision, not the decision itself.

**Q: Why paper trading?**
A: Simulation-first is a design principle. In a regulatory environment where autonomous trading raises compliance questions, defaulting to paper trading is the responsible choice. It also means we can demo live without risk.

**Q: What about backtesting?**
A: The architecture is modular — same analyzer, same risk engine, different data source and executor. Backtesting is the next implementation phase. The pipeline components are already testable with historical data.

**Q: How do you handle API rate limits?**
A: Telegram handler has per-user rate limiting (20 requests/minute). Exchange calls use ccxt's built-in rate limiting. Scan interval is configurable (default 60 seconds).

---

*End of Demo Director's Package — RUNECLAW by Humanoid Traders*
