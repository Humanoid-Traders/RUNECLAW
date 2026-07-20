# Live-Testing Readiness — the switchboard, with evidence (LIVE-1)

Operator directive: default ON everything we can justify, start live
testing, protect every linked test account by a max-funds cap.
"All we can" means all we can *prove* — every decision below carries its
reason.

## Flipped ON in this pass (protection-only, no money-path writes)

| Flag | Now | Why it is safe |
|---|---|---|
| `GUARDIAN_FIREWALL_ENABLED` | ON | Injection screening for chat-driven actions in **warn mode** — `GUARDIAN_FIREWALL_BLOCK_HIGH` stays opt-in, so nothing is blocked by default, only flagged. |
| `GUARDIAN_DIGITAL_TWIN_ENABLED` | ON | Read-only stress simulation. Observes, never trades. |
| `GUARDIAN_RISK_SENTINEL_ENABLED` | ON | Intra-book crowding detection — alerts only. |
| `GUARDIAN_ESCAPE_ENABLED` | ON | Emergency-exit **plan generation** — a recommendation surface, never an execution path. |

All four shipped with full test suites (Guardian PR-3..6); the gating
suites pass against the new defaults (35 tests re-run in this change).

## Already ON (the standing protection stack for linked test accounts)

- **Per-account max-funds cap** — `PER_USER_MAX_FUNDS_USD` (default $100):
  total deployed margin per linked account can never exceed it (preflight,
  audited when it blocks). *This change, part 1.*
- Per-user loss breakers wired to each account's own PnL (C1).
- Sizing clamped to the executing account's own balance (C2).
- Per-user kill re-check before every execution (C4).
- Fail-closed uniform 5x leverage: unconfirmed leverage aborts the order;
  `/leverage` clamped 1–20x.
- Micro caps on the operator account (`MICRO_MAX_*`).
- Secrets vault, backups, audit hash-chain, flight recorder — all ON.

## Deliberately OFF, with reasons

| Flag | Stays | Reason |
|---|---|---|
| `ONCHAIN_FLOW_ENABLED` | OFF | New-voter class: signal-changing, needs live-shadow evidence first. |
| `DYNAMIC_LEVERAGE_ENABLED` | OFF | Operator chose one uniform standard (2026-07-20). |
| `LEVERAGE_FAIL_OPEN` | OFF | Fail-closed IS the protection. |
| `INTENT_POLICY_ENABLED`, `AUTHORITY_ENVELOPE_ENABLED` | OFF | Enforcement layers that can block trades; enable only after the operator authors a policy/envelope (`/policy`). |
| `GUARDIAN_FIREWALL_BLOCK_HIGH` | OFF | Escalates warn→block; flip after reviewing warn-mode hit quality. |
| `EQUITY_CURVE_BREAKER`, `DRAWDOWN_RECOVERY`, `EQUITY_THROTTLE`, confidence-floor / correlation-intent / fee-gate / reentry-cooldown / VaR flags | OFF | Behavior-changing; A/B evidence mixed or venue-dependent — enable per explicit A/B, not as a batch. |
| `AUTO_CONFIRM_THRESHOLD=1.0`, `AUTO_CONFIRM_LIVE_ENABLED=false` | OFF | Auto-execution of real money is the operator's explicit switch, never a default. |
| `PER_USER_LIVE_ENABLED` | OFF | **The master switch for the live test.** Flip it when you start. |

## Start-the-test checklist (operator)

1. Set `PER_USER_MAX_FUNDS_USD` to the per-account risk you actually want.
2. Add testers to `LIVE_TRADER_TELEGRAM_IDS` (NOT `ADMIN_TELEGRAM_IDS`).
3. `PER_USER_LIVE_ENABLED=true`, restart, then `/approve` + `/grant_live`
   each tester.
4. Confirm on `/status` that all components read healthy.
5. Watch the first trades in the flight recorder; the caps will announce
   themselves in the audit log when they block.
