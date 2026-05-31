# AI Learning System

RUNECLAW includes a **self-improving AI learning system** with 8 integrated modules, all governed by an immutable safety policy. The system can observe, reflect, and propose improvements -- but it can **never override the risk engine**.

**Try it live:** Send `/learn` to [@HTRUNECLAW_bot](https://t.me/HTRUNECLAW_bot)

---

## The 8 Modules

### 1. Experience Memory
Every trade decision is logged with full market context -- entry conditions, indicator values, regime, confluence score, and outcome. Append-only JSONL format ensures immutability.

### 2. Reflection Engine
Post-trade analysis generates structured lessons: Was the signal valid? Was sizing correct? What would have been different with tighter stops? Reflections feed into strategy scoring but **may recommend, never auto-apply**.

### 3. Strategy Evaluator
Risk-adjusted scoring with S/A/B/C/D tier rankings based on:
- Safety score (weighted highest)
- Sharpe ratio
- Maximum drawdown
- False positive rate
- Overfitting detection (flags strategies with < 10 trades)

A strategy is **never promoted based on profit alone**.

### 4. Pattern Learner
Detects recurring market patterns across regimes -- time-of-day effects, correlation breakdowns, volume patterns. Observations only. **Patterns may NEVER override the risk engine** (enforced via Pydantic validator).

### 5. Model Comparer
Side-by-side accuracy tracking of rule-based vs LLM analysis. When models strongly disagree: `NO_TRADE_UNCERTAIN`. Safety over profit.

### 6. Prompt Optimizer
Version-tracks LLM prompts with performance scoring. Blocked from:
- Weakening fail-closed wording
- Removing safety disclaimers
- Creating profit guarantees

### 7. Macro Learner
Tracks crypto reactions to FOMC, CPI, NFP, and PCE events. Records 5-minute to 24-hour price reactions. Context only -- **never creates trade signals**.

### 8. Human Feedback
12 feedback types from `correct` to `too_risky`. Updates reflections, scores, and docs backlog. Cannot bypass risk gates.

---

## Safety Policy (Immutable)

The learning system operates under a hard-coded safety policy that cannot be modified at runtime:

| Allowed | Blocked |
|---------|---------|
| Improve explanations | Enable live trading |
| Suggest safer filters | Increase leverage |
| Detect recurring mistakes | Remove stop-losses |
| Rank strategies for review | Bypass risk engine |
| Auto-apply docs & tests | Delete audit logs |
| Produce improvement proposals | Claim guaranteed profit |

Every proposal is classified:
- `SAFE_AUTO_DOCS` -- auto-apply documentation updates
- `SAFE_AUTO_TEST` -- auto-apply test improvements
- `HUMAN_REVIEW_REQUIRED` -- needs admin approval
- `BLOCKED_RISK_INCREASE` -- automatically rejected
- `BLOCKED_COMPLIANCE_RISK` -- automatically rejected

The `may_override_risk` field is enforced `False` via Pydantic validator. Any attempt to set it to `True` raises a validation error at the schema level.

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/learn` | Full learning dashboard with module stats and strategy tiers |
| `/patterns` | Detected recurring market patterns and success rates |
| `/proposals` | Pending strategy improvement proposals |

---

## Architecture

```text
Trade Decision
     |
     v
Experience Memory (log)
     |
     v
Reflection Engine (analyze)
     |
     v
Strategy Evaluator (score)
     |
     v
Pattern Learner (detect)
     |
     v
Proposal Generation
     |
     v
Safety Policy Filter <-- BLOCKED if risk-increasing
     |
     v
Human Review (if required)
     |
     v
Apply (docs/tests only, never trading logic)
```

The learning system is **completely sandboxed** from the trading pipeline. It cannot modify risk parameters, enable live trading, or change execution logic.
