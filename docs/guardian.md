# RUNECLAW Guardian

> **The AI proposes. Deterministic controls authorize. The wallet enforces. The recorder proves. The escape agent recovers.**

Guardian is RUNECLAW's **safety, control, evidence, and recovery layer** for
autonomous crypto capital. It sits around the trading agent, not inside it: the
model may reason, propose, and converse, but a set of **pure, deterministic**
controls decides what is authorised, records what happened on a tamper-evident
ledger, foresees how the book would fare under stress, and plans a safe way out.

Every module shares the same discipline:

- **A pure, deterministic core** (`bot/guardian/*.py`) — no engine, exchange,
  clock, or network. Trivially unit-testable; it can never touch the trade path.
- **A thin, fail-open engine bridge** that seals a typed verdict to the
  tamper-evident audit chain. A fault degrades to a safe default; it never breaks
  a chat, a command, or a trade.
- **Default-OFF for chain writes.** With the flag unset, behaviour is
  byte-identical to before; the read-only assessment still computes on demand.
- **An admin, read-only surface** (a Telegram command) to see it working.

## The six modules

| Module | Question | Core | Chain event | Command | Flag |
| --- | --- | --- | --- | --- | --- |
| **Flight Recorder** | What *happened*? | `flight_recorder.py` | `DECISION` / `OUTCOME` | (web view) | — |
| **Intent Compiler** | What is *allowed*? | `intent_policy.py` | `POLICY_DECISION` | `/policy` | `INTENT_POLICY_ENABLED` |
| **Firewall** | What's coming *in*? | `firewall.py` | `FIREWALL` | (chat pre-scan) | `GUARDIAN_FIREWALL_ENABLED` |
| **Digital Twin** | What if the market *moves*? | `digital_twin.py` | `TWIN` | `/twin` | `GUARDIAN_DIGITAL_TWIN_ENABLED` |
| **Risk Sentinel** | Is the book *crowded*? | `risk_sentinel.py` | `SENTINEL` | `/sentinel` | `GUARDIAN_RISK_SENTINEL_ENABLED` |
| **Escape Agent** | How do we *get out*? | `escape_agent.py` | `ESCAPE` | `/escape` | `GUARDIAN_ESCAPE_ENABLED` |

### 1. Agent Flight Recorder — *evidence*

A SHA-256 hash-chained, Ed25519-attested append-only ledger of every financial
decision: the idea, the risk verdict, and the outcome, joined into flight
records the web can re-verify. The evidence layer the rest of Guardian writes to.
→ `docs/` (Flight Recorder web view + MCP tool).

### 2. Formal Strategy Intent Compiler — *policy*

Turns a plain-language intent ("only majors, max 5% per trade, stop if I'm down
8% this week") into a versioned, content-hashed set of typed rules the risk gate
enforces **deterministically**. A policy can only ever **tighten** the engine's
caps, never loosen them, and runs shadow-first. → `docs/guardian_intent_compiler.md`

### 3. Prompt-Injection & Transaction Firewall — *inputs*

A pure detector over the chat/NL action surface (the agent that can *act*),
classifying inbound text against known manipulation shapes (instruction override,
role hijack, exfiltration, action injection, hidden-char smuggling) before it can
steer a trade. Telemetry-first; blocking HIGH verdicts is a separate opt-in.
→ `docs/guardian_firewall.md`

### 4. Portfolio Digital Twin — *foresight*

Stress-tests the live book against parametric price shocks (flash crash,
correlated tail, alt capitulation, short squeeze), reporting projected drawdown
and exactly which positions would be liquidated — isolated-margin math, no
network. → `docs/guardian_digital_twin.md`

### 5. Systemic Risk Sentinel — *crowding*

Flags intra-book crowding — one sector holding too much, a heavily one-directional
book, same-group clusters, positions sharing a liquidation zone — the structural
fragility a single move would expose. → `docs/guardian_risk_sentinel.md`

### 6. Universal Escape Agent — *recovery*

Produces a safe, ordered emergency-exit plan: which position to close first and
why, ranked by liquidation urgency × exposure, with the margin each close frees.
Plan-only — execution stays with the existing kill-switch stack.
→ `docs/guardian_escape_agent.md`

## The console — `/guardian`

`engine.guardian_status()` composes all six into one read-only safety-posture
snapshot: the evidence chain's length and verification, the intent policy's
state, the firewall's arming, and the live book's foresight / crowding / unwind
urgency — plus which modules are armed. The `/guardian` admin command renders it
on one screen. **Viewing the console seals nothing** — it calls the *pure* modules
directly, so a status view has no side effects. Deep-dive with `/twin`,
`/sentinel`, `/escape`, `/policy`.

## Design invariants

- **Tighten-only.** No Guardian module can loosen an engine control. The Intent
  Compiler only appends rejections; the detectors only warn or record; the Escape
  Agent only plans. The engine's own risk gate remains authoritative.
- **Fail-open, always.** Guardian is a safety layer, not a dependency. Every
  module is wrapped so a bug in it can never halt trading or break a chat.
- **Provable, not asserted.** Verdicts are sealed to a hash-chained, attested
  ledger, so what the controls decided is auditable after the fact.
- **Deterministic authority.** The model proposes; pure functions of the inputs
  decide. No LLM sits on the enforcement path.

## Enablement

Every flag defaults **OFF** (chain writes) and is mirrored in
`config/risk_manifest.yaml`. Recommended rollout per module: enable the flag to
observe (telemetry/shadow) first, read the verdicts on the evidence chain and via
the admin commands, then — only where a module supports it (the Intent Compiler's
enforce mode, the Firewall's block-high) — turn on enforcement. Config is frozen
at import; changing a flag needs a restart.
