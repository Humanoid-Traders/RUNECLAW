# Guardian — Scoped, Revocable, Non-Custodial Authority (Authority Envelope)

> Nothing moves user funds without a human-set, revocable authority envelope.
> The AI proposes. **Deterministic controls authorize.** The wallet enforces.

## What this is

The **Authority Envelope** is the custody-boundary layer of Guardian. It is the
complement to the Formal Strategy Intent Compiler (`intent_policy.py`):

| Layer | Question it answers | Failure mode |
|---|---|---|
| Intent Compiler | *What trades are allowed?* (strategy discipline) | fail-**open** per rule — a bad rule never halts trading |
| **Authority Envelope** | *What is the credential even permitted to do?* (custody) | fail-**closed** — in doubt, **DENY** |

An Authority Envelope is a **human-set, versioned, content-hashed** grant that
bounds what a linked exchange key or wallet session may do:

- `allowed_venues` — the only venues this authority covers (∅ ⇒ none).
- `allowed_market_types` — e.g. `swap`, `spot` (∅ ⇒ none).
- `max_notional_per_trade_usd` — hard per-action size ceiling.
- `max_notional_daily_usd` — rolling 24h spend ceiling (caller supplies spend).
- `withdraw_allowed` — **default `false`, a hard line.** A withdrawal/transfer
  action is denied unless this is explicitly `true` *and* the destination is on
  `withdraw_allowlist`.
- `expiry_ts` — the authority self-expires (session-key semantics).
- `revoked` — a human kill-switch; once set, every action is denied.
- `symbol_allowlist` / `symbol_blocklist` — optional per-asset scoping.

### The two invariants (mechanical)

1. **Tighten-only.** `compile_envelope` clamps every ceiling against the engine's
   authoritative cap and against the venues the platform actually supports. An
   envelope can only ever be *at least as restrictive* as the engine — the AI can
   never author itself more authority than a human already granted.
2. **Fail-closed.** `authorize()` returns `deny` for a missing/None envelope, an
   expired or revoked envelope, an unknown action kind, a malformed action, or
   any ceiling breach. `allow` is only ever returned when **every** check passes.
   Withdraw is denied by default and must be *doubly* opted in (flag + allowlist).

This maps directly to the ULTRA non-goal: *nothing that moves user funds without
a human-set, revocable authority envelope.*

## Bitget least-privilege mapping (preflight — PR-2)

Bitget API keys separate **read / trade / withdraw** permissions, support an IP
allowlist (≤20) and ≤10 keys per account. The envelope's `withdraw_allowed=false`
is meant to line up with a key minted **without** withdraw permission. The
preflight **reconciles the envelope against the key's *observable* posture** and
reports each dimension honestly:

- `read` → **CONFIRMED** by the existing read-only balance probe.
- `environment` (live vs demo) → **CONFIRMED / VIOLATION** from the probe.
- `withdraw` → **UNVERIFIED** unless a privileged key-info endpoint confirms the
  granted scope. We never attempt a withdrawal to test it — an honest
  `UNVERIFIED` beats invented proof (same discipline as Proof-of-PnL).

## Staged enforcement (default OFF)

Like the Intent Compiler, the envelope has three modes so wiring is safe:

- `off` — not consulted (default; the operator path is byte-identical).
- `shadow` — `authorize()` runs and its `deny`s are **recorded** but not enforced.
- `enforce` — a `deny` blocks the action before it reaches the venue.

PR-1 ships the **pure core + tests** (blocks nothing). Enforcement wiring lands in
a later, separately-gated PR after shadow-mode evidence.

## Pre-registered predictions (before the tests were written)

Discipline rule: register the prediction before the run. For a deterministic
module the "run" is the test suite; these are the assertions pinned in advance.

- **A1 — withdraw is denied by default.** An envelope authored with no
  `withdraw_allowed` denies every `withdraw`/`transfer` action, regardless of
  size or destination. *Falsifier:* any withdraw allowed without an explicit
  `withdraw_allowed=true` **and** an allowlisted destination.
- **A2 — tighten-only compile.** A spec asking for `max_notional_per_trade_usd`
  above the engine cap, or a venue the platform doesn't support, is clamped/
  dropped with a warning — never widened. *Falsifier:* a compiled envelope more
  permissive than its spec's engine cap.
- **A3 — fail-closed.** `authorize(None, …)`, an expired envelope, a revoked
  envelope, and an unknown action kind all return `deny`. *Falsifier:* any of
  these returning `allow`.
- **A4 — daily ceiling.** With `spent_today_usd` supplied, a trade whose notional
  would push the 24h total past `max_notional_daily_usd` is denied even though it
  is individually under the per-trade cap. *Falsifier:* the sum exceeding the
  daily cap while returning `allow`.
- **A5 — determinism + identity.** `compile_envelope` of the same logical spec
  yields the same `envelope_id`/hash on any machine; changing one ceiling changes
  the hash. *Falsifier:* an unstable hash, or two different envelopes colliding.

Results are recorded in `tests/test_guardian_authority.py`.
