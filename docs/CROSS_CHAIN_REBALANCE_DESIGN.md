# Envelope-Gated Autonomous Cross-Chain Yield Rebalancing — Design Doc

> The AI proposes. Deterministic controls authorize. The wallet enforces. The
> recorder proves. The escape agent recovers.

Status: **DESIGN ONLY (CROSS-3)** — no money-path code ships with this document.
It specifies the safety architecture that a later, separately-gated
implementation (CROSS-2 for guided/operator-signed moves; a further slice for
autonomy) must run through. Writing the envelope down *before* the money-path is
deliberate: the guarantees below are the acceptance criteria for that code.

---

## 1. Problem statement

RUNECLAW can already *see* better yield across chains but cannot *act* on it
without a human at every step. Today:

- **CROSS-1** (`app/lib/cross_yield.js`, `bot/core/idle_yield.py`) produces
  ranked, **net-of-cost** rebalance opportunities — read-only, `read_only:true`,
  move costs explicitly labelled ESTIMATES. It never moves funds.
- **`/stake`** (`bot/core/yield_radar.py`) is the only live money-path, and it is
  single-venue (Bitget Earn), stables-only, 30%-reserve-clamped, and requires an
  **explicit admin confirm with amounts recomputed at press**.

There is **no autonomous rebalancer**. The gap the operator wants closed: let the
agent *execute* a worth-moving cross-chain reallocation **without** a per-move
human confirm — but only ever inside a human-set, revocable authority envelope,
on testnet first, with every move recorded and instantly reversible.

Autonomy without a hard, mechanical, human-set boundary is exactly the failure
mode §4 forbids. This document defines that boundary so autonomy becomes a
*bounded* capability rather than an open one.

## 2. Why this is a real project, not a config swap

The pieces exist but were each built to *stop short* of autonomous execution:

- the scanner is read-only by construction (`planMoves` returns plans, not
  actions);
- the signer is admin-only, testnet-only, and per-action envelope-gated
  (`web3_signer.evaluate_sign`);
- the Authority Envelope authorizes **one action at a time**
  (`authority.authorize`), it does not run a loop.

Autonomy means *removing the human from the per-move loop* while keeping the
human's authority binding on it. That is a new control-flow — a bounded
execution loop — not a flag. It must be designed, not toggled.

## 3. Two invariants (mechanical)

Everything below serves two invariants that must hold for **every** autonomous
move, checked in code, fail-closed:

1. **Triple-gate.** A move executes only when *all three* hold, evaluated
   independently, any failure → skip:
   - the scanner marks it `worth: 'yes'` with a **positive** net-of-cost return
     over the policy horizon (`cross_yield.breakeven().net_horizon_usd > 0`);
   - the compiled **yield policy** returns `verdict == 'pass'`
     (`intent_policy.evaluate_policy`);
   - the **Authority Envelope** returns `decision == 'allow'` for the move as a
     `transfer`/`withdraw` action (`authority.authorize`).
2. **Human-set daily budget bounds autonomy.** Cumulative moved notional per UTC
   day never exceeds the envelope's `max_notional_daily_usd`. The envelope
   already tracks `spent_today_usd` inside `authorize()`; the loop feeds it the
   running total, so the (N+1)-th move that would breach the ceiling is denied by
   the same deterministic gate — no separate counter to drift.

If either invariant cannot be evaluated (missing envelope, malformed plan, stale
price), the move is **skipped**, never guessed. The loop fails safe to inaction.

## 4. The layered architecture (mapping to real components)

| Layer | Role | Component (exists today) | Autonomy change |
|---|---|---|---|
| **Propose** | rank net-of-cost moves | `app/lib/cross_yield.js` `planMoves`/`breakeven`/`moveCostUsd`; `bot/core/idle_yield.py` | none — reused read-only |
| **Policy** | NL yield goal → deterministic rules | `bot/guardian/intent_policy.py` `compile_nl`/`compile_policy`/`evaluate_policy` | add yield-specific rule types (§5) |
| **Authority** | credential-level cap + allowlists | `bot/guardian/authority.py` `authorize`/`compile_envelope`/`revoke` | none — reused as-is |
| **Execute** | testnet sign + broadcast | `bot/web/web3_signer.py` `prepare_tx`/`build_and_sign`/`broadcast` | called by the loop, still per-move `evaluate_sign`-gated |
| **Prove** | record every move | `bot/guardian/review_queue.py` `ReviewQueue.record`; `bot/guardian/flight_recorder.py` | one record per proposed + executed move |
| **Recover** | kill-switch + exit | `authority.revoke`; Universal Escape Agent (`docs/guardian_escape_agent.md`) | revoke halts the loop within one tick |

The autonomy loop is thin glue over these — it introduces **no new** signing,
capping, or pricing primitive. If a move can't be expressed as an existing
`authorize()` action and an existing `build_and_sign()` call, it does not run.

## 5. The yield-policy surface — draft

Extends the Intent Compiler's rule catalogue (`bot/guardian/intent_policy.py`)
with yield-rebalance rule types (numeric, tighten-only, clamped against engine
caps like every existing rule):

```
# compiled from NL like:
#   "keep stables earning the best safe rate, only move when it clears costs in
#    30 days, never move more than $50 at once or $150 a day, testnet only"
rules = [
  {"type": "min_delta_apy_pct",        "value": 1.0},   # skip marginal moves
  {"type": "max_breakeven_days",       "value": 30},    # must pay back within horizon
  {"type": "min_net_horizon_usd",      "value": 0.01},  # strictly positive net
  {"type": "max_move_notional_usd",    "value": 50.0},  # per-move ceiling (≤ envelope)
  {"type": "max_daily_move_usd",       "value": 150.0}, # per-day ceiling (≤ envelope)
  {"type": "allowed_chains",           "value": ["sepolia","base-sepolia"]},
  {"type": "allowed_assets",           "value": ["USDC"]},
  {"type": "require_noncustodial",     "value": true},  # no custodial hops
  {"type": "require_recallable",       "value": true},  # no lockups (lockup_days==0)
]
```

`evaluate_policy(policy, ctx)` stays pure and per-rule fail-open (a rule that
can't be evaluated **skips**, leaving the engine/envelope floor intact). The
policy governs *which moves are permitted*; the envelope governs *what the
credential may do* — complementary, and a move needs **both**.

## 6. The autonomy loop — draft interface

```python
# bot/guardian/yield_rebalancer.py  (FUTURE — not in this PR)
async def run_rebalance_once(*, plans, policy, envelope, signer_env,
                             spent_today_usd, now_ts) -> RebalanceResult:
    """One deterministic pass. Pure decision + (phase 2+) testnet execution.
    Returns {proposed, authorized, executed, skipped:[{plan, reason}], recorded}.
    Never raises on a bad plan — that plan is skipped with a reason."""
```

- Iterate `plans` newest-worth-first (the order `planMoves` already returns).
- For each: run the **triple-gate** (§3). On any deny, append to `skipped` with
  the first failing reason and continue.
- Every proposal (pass or skip) is written to the **Flight Recorder** and, when
  it would execute, to the **Guardian review queue** *before* signing — so the
  evidence exists even if the broadcast later fails.
- Execution (Phase 2+) calls `evaluate_sign` → `prepare_tx` → `build_and_sign`
  → `broadcast`, which re-checks testnet at every step (the 4 mainnet-block
  layers, §7). The loop passes the running `spent_today_usd` so invariant 2 is
  enforced by `authorize()` itself.

## 7. Why this is safe — the mainnet wall (unchanged, inherited)

Autonomy inherits the signer's four independent mainnet-block layers verbatim —
this design adds **nothing** that can reach mainnet:

1. `web3_exec_gate.mainnet_allowed` (`WEB3_LIVE_EXEC_ALLOW_MAINNET`) defaults
   **OFF**;
2. `evaluate_sign.testnet_only` requires `net.testnet` True with **no** mainnet
   override, regardless of the allow-flag;
3. `build_and_sign` / `broadcast` re-check `chain_id ∈ _TESTNET_CHAIN_IDS`
   (derived from the gate's `NETWORKS`, so they cannot drift);
4. every move still runs `authorize()` as a `transfer`/`withdraw`, so the
   Authority Envelope's `allowed_venues` / dest-allowlist / withdrawal
   double-opt-in apply on top.

A mainnet autonomy slice is **explicitly out of scope** here and requires its own
design doc, a tighter envelope, and an operator sign-off gesture.

## 8. Staged enforcement (default OFF)

| Phase | What runs | Signs? | Default |
|---|---|---|---|
| **0 — this doc** | design only | no | n/a |
| **1 — shadow** | scanner + policy + `authorize()` run each tick; every decision recorded to Flight Recorder + review queue | **no** (dry-run) | OFF |
| **2 — testnet exec** | authorized moves signed + broadcast on **testnet only**, per-move gated, recorded | testnet only | OFF |
| **3 — mainnet** | separate future doc; separate allow-flag; tighter envelope; operator sign-off | — | OFF |

Phase 1 is a pure observability layer: it proves the triple-gate produces the
moves a human would approve, *before* any key touches a transaction. Promotion
1→2 is an operator act (flag + testnet key + RPC + bound enforce-mode envelope),
mirroring how the signer itself was activated.

## 9. Recovery

- **`authority.revoke(envelope)`** is the kill-switch: it re-hashes the envelope
  so the running loop's next `authorize()` fails closed within one tick. Autonomy
  stops without a redeploy.
- The **Universal Escape Agent** (`docs/guardian_escape_agent.md`) produces a
  dependency-aware unwind plan if a rebalanced position needs emergency exit.
- Because every move is in the **Flight Recorder** (tamper-evident hash chain)
  and the **review queue**, an operator can reconstruct and audit exactly what
  the agent did, when, under which envelope hash, and why.

## 10. Test coverage this plan implies (per phase, when built)

- **Phase 1:** triple-gate unit tests — a move passes only with all three of
  (scanner-worth, policy-pass, envelope-allow); each single failure skips with
  the correct reason. Daily-budget invariant: the move that would breach
  `max_notional_daily_usd` is denied while earlier ones allowed. Policy compile:
  new yield rule types clamp tighten-only. Loop never raises on a malformed plan.
- **Phase 2:** the loop calls `build_and_sign`/`broadcast` **only** for
  triple-gated moves; a mainnet chain is refused at every layer even with the
  feature ON; the review-queue + Flight-Recorder entry exists **before** the
  broadcast; `spent_today_usd` threading is exact.
- Property test (mirroring `tighten_envelope`): the loop can never execute a move
  the envelope+policy intersection forbids.

## 11. Pre-registered predictions (before Phase 1 is written)

1. In shadow, ≥ 95% of moves the triple-gate would authorize will match what the
   existing `/stake`-style human review would approve on the same data (few false
   positives) — because the gate is *stricter* than the human path, not looser.
2. The dominant skip reason will be `min_net_horizon_usd`/`max_breakeven_days`
   (cost eats the delta), not envelope denial — i.e. economics, not authority,
   is the usual binding constraint on small balances.
3. Zero mainnet touches across the entire Phase 2 test matrix.

(Empirical results + disposition to be appended when Phases 1–2 land, per house
style.)

## 12. Open questions for the operator before Phase 1

1. **Horizon.** Default rebalance horizon — 30 or 90 days? (`breakeven` defaults
   to 90; the example policy tightens to 30.)
2. **Cadence.** How often should the shadow loop tick — hourly, or only when the
   scanner surfaces a new worth-moving row?
3. **Scope.** Stables-only to start (matching `/stake`), or any allowlisted
   asset?
4. **Custody.** Hard-require non-custodial + recallable (no lockups) for
   autonomous moves? (Design assumes **yes**.)
5. **Envelope authorship.** Compile the yield policy from NL (`compile_nl`) with a
   human review-then-compile step, or hand-author the JSON for the first slices?
