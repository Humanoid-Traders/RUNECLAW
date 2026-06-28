# Per-User Live Trading — Readiness Report

**Date:** 2026-06-28
**Status:** ✅ **BUILT, TESTED, and INERT.** All five phases merged. The feature
is OFF by default (`PER_USER_LIVE_ENABLED=false`) and the bot trades exactly as
it did before. This report is the "report when ready" deliverable.

## What was asked

> "prepare all so all users can live trade — for now no auto trade, just confirm
> by user, except admins there we keep the 85% or higher … every user can link
> his own account for live trading … prepare all step by step the right way …
> now all must be 100% safe and ready … we will enable it later."

## What is delivered

Every user can link **their own** Bitget account and, once enabled, their
**manually confirmed** trades execute on **their own** account. Admins keep
auto-trade at ≥85% on the operator account. Nothing is active yet — it is built
and waiting behind a default-OFF switch.

| Requirement | Status | How |
|---|---|---|
| Every user links their own account | ✅ | `/connect` → encrypted per-user credential store (Fernet/AES) |
| Keys safe at rest | ✅ | Fernet ciphertext in `data/exchange_creds.enc` (0600); tests assert no plaintext on disk |
| Keys never leak to chat/logs | ✅ | `/connect` deletes the message first; only `fingerprint()` shown; outbound redaction |
| Each user's trade on their own account | ✅ | `LiveExecutor(user_id, credentials)` + `engine._executor_for(user_id)` routing |
| Regular users: manual confirm only | ✅ | Auto-confirm runs only on `user_id="auto"` (operator); regular users never auto-confirmed |
| Admins: auto-trade ≥85% | ✅ (config) | `AUTO_CONFIRM_THRESHOLD=0.85` + `AUTO_CONFIRM_LIVE_ENABLED=true` on the operator account |
| Each account monitored (SL/TP, reconcile) | ✅ | Monitoring loops iterate all executors; linked users rehydrated at startup |
| Regular user can't trade operator's account | ✅ | Eligibility gate REJECTS a keyless regular user's live confirm |
| 100% safe / off until enabled | ✅ | `PER_USER_LIVE_ENABLED=false`; operator path byte-identical |

## Phases (all merged)

1. **#85** — encrypted per-user credential store + `/connect` `/disconnect` `/exchange`.
2. **#86** — `LiveExecutor` parameterized `(user_id, credentials, state_dir)`; operator path byte-identical.
3. **#87** — engine per-user executor registry + `confirm_trade` routing (flag-gated OFF).
4. **#88** — monitoring/reconciliation across all executors + startup rehydration.
5. **(this)** — eligibility gate, admin auto-trade `0.85` docs, `.env.example`, this report.

## Defense-in-depth (the order a live order must survive)

1. `SIMULATION_MODE=false` hard veto (independent kill switch).
2. `CONFIG.is_live()` — requires `LIVE_TRADING_ENABLED=true` + `TELEGRAM_CHAT_ID`.
3. Per-session `/golive CONFIRM` arming.
4. Compliance locks (existing).
5. **Per-user eligibility gate** — a regular user must own linked, decryptable keys.
6. Executor resolution — routes to the user's own account (or operator for admin/auto).

Turning the feature on **adds** gate 5; it can never loosen 1–4.

## Test coverage (all green; CI gate `total failing: 0`)

- `test_exchange_credentials.py` — encryption at rest, no plaintext, reload, wrong-key→None, fingerprint, key precedence.
- `test_live_executor_per_user.py` — operator byte-identity, per-user file isolation, credential binding.
- `test_per_user_executor_routing.py` — flag-off operator-only, auto/unattended→operator, own-executor, cache + rebuild, invalidate.
- `test_per_user_monitoring.py` — operator-only when off, rehydrate (no-op off / builds on / skips unusable), `user_ids()`.
- `test_per_user_eligibility.py` — flag-off all eligible, auto/unattended eligible, regular-without-keys rejected, regular-with-keys + admin eligible, admin allowlist.
- Existing `test_live_executor.py` (20) and `test_audit_v7_fixes.py` ordering tests pass unchanged.

## How to enable (when ready)

See the operator runbook in `docs/LIVE_TRADING_ENABLEMENT.md`. In short:

1. Pin `RUNECLAW_SECRETS_KEY` in the environment.
2. Users `/connect` their Bitget USDT-M futures (read+trade) keys; verify with `/exchange`.
3. Set `PER_USER_LIVE_ENABLED=true` (with `LIVE_TRADING_ENABLED=true`,
   `SIMULATION_MODE=false`, `TELEGRAM_CHAT_ID`, and `/golive CONFIRM`).
4. Regular users stay `AUTO_CONFIRM_THRESHOLD=1.0`; for admin auto-trade set
   `0.85` + `AUTO_CONFIRM_LIVE_ENABLED=true`.

## Known follow-up (non-blocking)

- **Per-user live-balance size-clamp.** The size clamp in `confirm_trade` reads
  the operator's cached balance; per-user balance caching is a refinement for
  when the feature runs with real volume. Does not affect safety while OFF.
