# Guardian — Universal Escape Agent

> The AI proposes. Deterministic controls authorize. The escape agent recovers.

When something goes wrong — a crash, a compromised key, an operator who just wants
*out* — the question is not only "close everything" but **in what order**, so the
unwind itself doesn't make things worse. The Escape Agent produces a **safe,
ordered emergency-exit plan**: which position to close first, why, and how much
margin each close frees for the positions still open.

This is the **recovery** capstone of Guardian — the sixth and final module of the
research report's stack:

> The AI proposes. Deterministic controls authorize. The wallet enforces. The
> recorder proves. The escape agent recovers.

## The planner — `bot/guardian/escape_agent.py`

Pure, deterministic (no engine, no exchange, no clock, no network). `plan(positions)`
ranks the book by **escape urgency** — a risk-weighted blend of:

- **fragility** — how close each position sits to its own isolated-margin
  liquidation (reusing the Digital Twin's canonical `liquidation_move_frac`), and
- **exposure** — how large the position is.

so `urgency = notional × (0.10 / liquidation_move_fraction)`. The most dangerous
positions are unwound first, and each close frees isolated margin that widens the
liquidation buffer on everything still open — the plan shows the **cumulative
margin freed** at each step as the reason the order is safe.

Every step carries *why* it is where it is (fragility, exposure share, margin
freed), so the plan is auditable, not a black box. The book's overall unwind
urgency (`risk`: none / low / medium / high) is keyed on the most fragile
position's distance to liquidation.

## Plan-only — it closes nothing

This module **describes** the exit; it never pulls the trigger. Execution stays
with RUNECLAW's existing, battle-tested kill-switch stack:

| Primitive | Scope |
| --- | --- |
| `engine.flatten_all_positions()` | close every position across all accounts (reduce-only) |
| `executor.close_all_positions()` | close every position on one account |
| `engine.emergency_halt_all()` | trip the circuit breaker **and** flatten |
| `executor.close_position(trade_id)` | close one position |
| `executor._partial_close(...)` | reduce-only partial (staged exits) |

The plan **names** the recommended primitive; a human (or a later, explicitly
gated executor) acts on it. Telegram surfaces this: `/escape` shows the plan,
`/closeall` and `/emergency_stop` execute.

## The guarantees

1. **Pure + deterministic.** Trivially unit-testable; can never touch the trade
   path.
2. **Plan-only.** It ranks and explains; it closes nothing.
3. **Fail-open.** A position missing usable inputs is skipped; any fault degrades
   to an empty plan.
4. **Off by default for the chain.** The plan always computes on demand (the admin
   `/escape` command). An `ESCAPE` plan is *sealed* to the tamper-evident chain
   only when `GUARDIAN_ESCAPE_ENABLED` is on.

## Where it runs

- **Telegram** — `/escape` (admin, read-only) shows the ordered exit plan with
  per-step reasons and cumulative margin freed. Seals an `ESCAPE` plan when the
  flag is on.
- **Engine** — `engine.run_escape_agent(user_id="")` normalises the live book
  (shared `_twin_positions`), runs the pure planner, and records the plan when
  enabled.

## Enabling the chain record

```bash
# /escape works regardless — it's a read-only plan. This flag only controls
# whether each run also seals an ESCAPE plan to the evidence chain.
GUARDIAN_ESCAPE_ENABLED=true
```

Read from `CONFIG.risk` (frozen at import — restart to change) and mirrored in
`config/risk_manifest.yaml`.

## Why plan-only first

An auto-executing escape agent is a loaded gun pointed at the book. Shipping the
**planner** first — pure, deterministic, auditable, recorded — means the ordering
logic is proven and observable on the evidence chain before any wiring lets it
pull the trigger. Execution remains behind the existing human-confirmed kill
switches until an explicitly gated executor is added in a later, separate change.
