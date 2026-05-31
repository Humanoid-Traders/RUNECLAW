# AI Learning System — The 8 Sandboxed Modules

RUNECLAW includes a **self-improving AI learning system** with 8 integrated modules, all governed by an immutable safety policy. The system can observe, reflect, and propose improvements -- but it can **never override the risk engine**.

This is what turns RUNECLAW from a static rule+LLM trader into a true self-improving agent -- while staying 100% sandboxed.

**Try it live:** Send `/learn` to [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

---

## The 8 Modules (In Detail)

### 1. Experience Memory

Every single trade decision (scan -> analyze -> risk -> confirm -> execute) is logged with full market context: price action, indicator values, regime, volume profile, LLM thesis, risk scores, human confirmation, and final outcome.

This creates a permanent, queryable memory bank so the agent can recall: *"What happened last time we saw this exact confluence on SOL in a TREND_UP regime?"*

**Implementation:** `bot/learning/experience.py` -- `ExperienceMemory` class. Append-only JSONL format ensures immutability. Each record includes:
- Entry conditions (price, ATR, volume)
- All 10 confluence voters' values
- Regime classification (TREND_UP / RANGE / VOLATILE / etc.)
- Risk engine verdict + which checks passed/failed
- Human confirmation (approved/rejected)
- Final PnL outcome (if trade was taken)

---

### 2. Reflection Engine

After every closed trade (or end-of-day), the agent runs a post-trade analysis and generates structured lessons + improvement proposals.

It asks itself: *"Why did this win/lose? What rule or prompt change would have improved the R:R? Was the signal valid? Was sizing correct? What would have been different with tighter stops?"*

Reflections feed into strategy scoring but **may recommend, never auto-apply**.

**Implementation:** `bot/learning/reflection.py` -- `ReflectionEngine` class. Produces `ReflectionMemory` objects with:
- Signal quality assessment (was the entry actually good?)
- Sizing analysis (was risk/reward correctly calibrated?)
- Counterfactual analysis (what if stops were tighter/wider?)
- Recommended improvement (proposal fed to safety pipeline)

---

### 3. Strategy Evaluator

Risk-adjusted scoring with **S/A/B/C/D tier** rankings. A strategy is **never promoted based on profit alone**.

S-tier strategies get prioritized in future scans; D-tier ones get de-emphasized or retired.

**Scoring dimensions:**
- Safety score (weighted highest)
- Sharpe ratio
- Maximum drawdown
- Win rate
- False positive rate
- Overfitting detection (flags strategies with < 10 trades)

**Implementation:** `bot/learning/strategy_eval.py` -- `StrategyEvaluator` class. Produces scorecards with composite grades.

---

### 4. Pattern Learner

Detects recurring market patterns across different regimes (TREND_UP, RANGE, VOLATILE, etc.).

It builds a growing library of high-probability setups. For example: *"Bull Flag + OBV divergence on 15m after NFP appears 9 times with 78% win rate."* These patterns feed the confluence voter model.

Observations only. **Patterns may NEVER override the risk engine** (enforced via Pydantic validator on `may_override_risk` field -- hardcoded `False`).

**Implementation:** `bot/learning/patterns.py` -- `PatternLearner` class. Records `PatternRecord` objects with:
- Pattern type (candlestick, indicator divergence, volume anomaly, etc.)
- Regime context (which market conditions produced this pattern)
- Win rate and sample size
- `is_experimental` flag for patterns with < 10 occurrences
- Confidence score (higher = more validated)

---

### 5. Macro Learner

Tracks how crypto (especially Solana ecosystem tokens) reacts to major macro events: **FOMC, CPI, NFP, PCE**, ETF flows, etc.

Maintains a calendar-aware knowledge base so the agent can say: *"Last 3 NFP prints caused +4.2% average SOL move in the first 2 hours -- adjust volatility guard accordingly."*

Records 5-minute to 24-hour price reactions. **Context only -- never creates trade signals.**

**Implementation:** `bot/learning/macro_learner.py` -- `MacroLearner` class. Tracks:
- Event type and date
- Pre-event price levels (1h, 4h before)
- Post-event price reactions (5min, 1h, 4h, 24h)
- Volatility impact (ATR change)
- Historical average reactions per event type

---

### 6. Model Comparer

Runs side-by-side accuracy tracking between the rule-based indicators and the LLM thesis.

Over time it learns: *"GPT-4o-mini is better on macro context but worse on micro candlestick patterns than our custom Bollinger+ATR rules."* This data drives the tiered LLM routing in the token optimizer.

When models **strongly disagree**: `NO_TRADE_UNCERTAIN`. Safety over profit.

**Implementation:** `bot/learning/model_compare.py` -- `ModelComparer` class. Tracks:
- Rule-based direction vs LLM direction per trade
- Agreement rate over time
- Accuracy per regime type
- Average token cost per model tier

---

### 7. Prompt Optimizer

Version-tracks every system/user prompt and scores their real-world performance.

Automatically evolves prompts: *"This version of the risk thesis prompt improved average confidence calibration by 11% -- promote to default."*

**Blocked from:**
- Weakening fail-closed wording
- Removing safety disclaimers
- Creating profit guarantees

**Implementation:** `bot/learning/prompt_opt.py` -- `PromptOptimizer` class. Maintains version history with:
- Prompt text + version number
- Performance metrics (confidence calibration, accuracy)
- A/B test results between versions
- Safety keyword enforcement (blocked keywords list)

---

### 8. Feedback Collector

Actively integrates human feedback from Telegram: your confirm/reject decisions, `/learn` commands, or manual notes via `/feedback`.

Closes the human-in-the-loop so the agent learns your personal trading style and risk tolerance over time.

**12 feedback types:**
`correct`, `incorrect`, `too_risky`, `too_conservative`, `good_rejection`, `bad_rejection`, `good_explanation`, `bad_explanation`, `timing_early`, `timing_late`, `size_too_large`, `size_too_small`

Cannot bypass risk gates.

**Implementation:** `bot/learning/feedback.py` -- `FeedbackCollector` class.

---

## The Safety Sandbox (Why It's Elite)

Every single proposal from any of the 8 modules must pass a strict safety policy before it can influence future decisions:

### Allowed vs Blocked

| Allowed | Blocked |
|---------|---------|
| Improve explanations | Enable live trading |
| Suggest safer filters | Increase leverage |
| Detect recurring mistakes | Remove stop-losses |
| Rank strategies for review | Bypass risk engine |
| Auto-apply docs & tests | Delete audit logs |
| Produce improvement proposals | Claim guaranteed profit |

### Proposal Classification

Every proposal is classified before action:

| Classification | Action |
|----------------|--------|
| `SAFE_AUTO_DOCS` | Auto-apply documentation updates |
| `SAFE_AUTO_TEST` | Auto-apply test improvements |
| `HUMAN_REVIEW_REQUIRED` | Queued for admin approval |
| `BLOCKED_RISK_INCREASE` | Automatically rejected |
| `BLOCKED_COMPLIANCE_RISK` | Automatically rejected |

### The Hard Safety Invariant

The `may_override_risk` field is enforced `False` via **Pydantic validator**. Any attempt to set it to `True` raises a validation error at the schema level. This cannot be bypassed at runtime -- it's enforced at the data model layer.

```python
@field_validator("may_override_risk")
@classmethod
def must_not_override_risk(cls, v):
    if v is True:
        raise ValueError("Learning system CANNOT override risk engine")
    return False
```

---

## Learning Workflow (10 Steps)

```text
 1. OBSERVE    Collect market/macro/strategy signals
 2. DECIDE     Run strategy + risk engine
 3. LOG        Write to experience memory (append-only)
 4. SIMULATE   Paper trade only (default)
 5. REVIEW     Generate reflection after result
 6. SCORE      Update strategy scorecard (S/A/B/C/D)
 7. LEARN      Create lessons + pattern detection
 8. VALIDATE   Check if proposal improves safety
 9. APPROVE    Auto-apply docs/tests; human review for logic
10. VERSION    Save with changelog and rollback plan
```

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/learn` | Full learning dashboard: module health, learning score, strategy tiers, proposals |
| `/patterns` | Detected recurring market patterns and success rates |
| `/proposals` | Pending strategy improvement proposals |
| `/feedback <id> <type> [text]` | Submit human feedback on a decision |

---

## Architecture

```text
Trade Decision
     |
     v
Experience Memory (log)        ← Module 1
     |
     v
Reflection Engine (analyze)    ← Module 2
     |
     v
Strategy Evaluator (score)     ← Module 3
     |
     v
Pattern Learner (detect)       ← Module 4
     |
     v
Macro Learner (context)        ← Module 5
     |
     v
Model Comparer (accuracy)      ← Module 6
     |
     v
Prompt Optimizer (evolve)      ← Module 7
     |
     v
Feedback Collector (human)     ← Module 8
     |
     v
Proposal Generation
     |
     v
Safety Policy Filter  ◄── BLOCKED if risk-increasing
     |
     v
Human Review (if required)
     |
     v
Apply (docs/tests only, never trading logic)
```

The learning system is **completely sandboxed** from the trading pipeline. It cannot modify risk parameters, enable live trading, or change execution logic. The orchestrator (`bot/learning/orchestrator.py`) coordinates all 8 modules and enforces the safety policy at every step.

---

## Why This Matters for Judges

Most hackathon agents are static -- they do the same thing on day 1 and day 100.

RUNECLAW is the only live, public, self-improving agent that is still 100% risk-first and cost-transparent. It learns aggressively inside the sandbox, never outside it. This demonstrates real agentic behavior without the "rogue AI" risks that make autonomous trading dangerous.
