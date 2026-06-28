# Live Trading Enablement — Per-User Accounts (Preparation)

> **Status: BUILT & INERT. All five phases merged; the feature is OFF.**
> Every switch described here defaults **OFF**. With the defaults unchanged the
> bot behaves exactly as it does today: a single shared operator account, paper
> mode unless explicitly taken live. The full per-user capability is now
> implemented and gated behind `PER_USER_LIVE_ENABLED=false`; this file is the
> design + runbook for turning it on, *the right way*, when we choose to. See
> `docs/PER_USER_LIVE_READINESS.md` for the readiness report.

## Goal

Let **every user live-trade on their OWN Bitget account**:

- Each user links **their own** Bitget API keys (`/connect`). Their confirmed
  trades execute on **their own** account — not a shared operator account.
- **Regular users: manual confirm only.** No auto-trade. A signal becomes an
  order only when that user taps confirm.
- **Admins: auto-trade at ≥ 85% confidence** stays available (operator policy).
- Keys are **encrypted at rest** and never logged or echoed back to chat.
- 100% safe by construction: the live execution path is **byte-identical** to
  today until the master switch is deliberately turned on.

## The master switches (all default OFF / safe)

| Switch | Default | Meaning |
|---|---|---|
| `SIMULATION_MODE` | `true` | Hard veto. While true, **no** real order can ever be placed. |
| `LIVE_TRADING_ENABLED` | `false` | Global enable for live execution (existing). |
| `PER_USER_LIVE_ENABLED` | `false` | **New.** Route a user's confirmed live trade to *their own* linked account instead of the shared operator account. |
| `/golive CONFIRM` | runtime, off | Per-session human authorization to arm live mode (existing). |
| `auto_confirm_threshold` | `1.0` (disabled) | Auto-execute at/above this confidence. `0.85` = the admin "85%" policy. |
| `auto_confirm_live_enabled` | `false` | Auto-confirm may touch *live* (vs paper) orders. |

`is_live()` already requires `SIMULATION_MODE=false` **and**
`LIVE_TRADING_ENABLED=true` **and** a configured `TELEGRAM_CHAT_ID`. Per-user
routing adds **one more** gate (`PER_USER_LIVE_ENABLED`) on top — it can never
*loosen* the existing gates, only narrow them.

## Phased plan

The work is split so each phase is independently shippable, reviewable, and
inert until the flag flips. **Phase 1 is this PR.**

- **Phase 1 — credential store + linking commands (this PR).**
  - `bot/core/exchange_credentials.py`: Fernet (AES) encrypted, per-Telegram-id
    Bitget credential store. Master key from `RUNECLAW_SECRETS_KEY` env, else a
    generated `data/.exchange_secret.key` (chmod 600, loud warning to pin it).
  - `validate_bitget_credentials()`: a **read-only** balance fetch that proves
    the keys authenticate before we ever store them. Never places an order.
  - Telegram commands `/connect`, `/disconnect`, `/exchange` (private chat only;
    the key-bearing message is deleted immediately; status uses a non-reversible
    `fingerprint()`, never the key).
  - `PER_USER_LIVE_ENABLED` config flag (default OFF).
  - **No execution wiring.** Storing keys changes nothing about how orders are
    placed today.

- **Phase 2 — parameterize `LiveExecutor` (DONE).** Accepts `(user_id,
  credentials, state_dir)`; per-user position / closed-trade files. The
  shared-operator code path stays byte-identical (same defaults, same files) —
  verified by the existing `test_live_executor.py` continuing to pass unchanged.

- **Phase 3 — engine per-user executor registry (DONE).** `engine` holds
  `dict[user_id -> LiveExecutor]`; `confirm_trade(user_id)` routes to *that
  user's* executor. **Entirely gated by `PER_USER_LIVE_ENABLED`** — OFF means
  the single-operator path runs exactly as before. `'auto'`/`''` (admin
  auto-trade, unattended) stay on the operator account.

- **Phase 4 — monitoring / reconciliation (DONE).** Position monitoring, SL/TP,
  and reconciliation iterate across all per-user executors; linked users are
  rehydrated at startup so their positions resume being monitored after a
  restart.

- **Phase 5 — enablement gating + access policy (DONE).** Eligibility gate in
  `confirm_trade`: when `PER_USER_LIVE_ENABLED` is on, a regular (non-operator)
  user must have linked, decryptable keys — otherwise their live trade is
  REJECTED rather than placed on the operator account. Admin auto-trade `0.85`
  documented (`.env.example`). Final runbook + readiness report
  (`docs/PER_USER_LIVE_READINESS.md`).

## Security guarantees (Phase 1)

- **Encrypted at rest.** Secrets are Fernet ciphertext in `data/exchange_creds.enc`
  (chmod 600). Unit tests assert the plaintext key/secret/passphrase never
  appear in the file.
- **Never in chat history.** `/connect` deletes the message carrying the keys
  *first* — before any auth/rate gate can early-return.
- **Never logged / echoed.** Only `fingerprint()` (a sha256-derived `BG-xxxx…xx`
  tag) is ever displayed. `get()` returns plaintext only to the execution layer
  and only at trade time.
- **Fail-closed decryption.** If the master key changes, `get()` returns `None`
  (treated as "not connected") rather than raising.
- **Private chat only.** `/connect` refuses to run in a group.
- **Validation before storage.** Keys are functionally checked with a read-only
  balance fetch before they are persisted; a bad key stores nothing.

## Operator runbook (all phases landed — flip the switch when ready)

All five phases are merged and inert behind `PER_USER_LIVE_ENABLED=false`. To
turn the feature on:

1. **Pin the encryption key.** Set `RUNECLAW_SECRETS_KEY` in the environment (a
   urlsafe-base64 Fernet key) so ciphertext survives a wiped `data/` dir and the
   key is managed explicitly. Until set, a key is auto-generated and a warning is
   logged.
2. **Have users link.** Each user runs `/connect <api_key> <api_secret>
   <passphrase>` in private chat with a Bitget **USDT-M futures (read+trade)**
   key. `/exchange` confirms status; `/disconnect` removes it. Validation is a
   read-only balance fetch — a bad key stores nothing.
3. **Keep `PER_USER_LIVE_ENABLED=false`** while users link and rehearse. Linking
   keys changes nothing about execution until the flag flips.
4. **Enable per-user routing** by setting `PER_USER_LIVE_ENABLED=true` (plus the
   existing `LIVE_TRADING_ENABLED=true`, `SIMULATION_MODE=false`,
   `TELEGRAM_CHAT_ID`, and a per-session `/golive CONFIRM`). Once on, the
   eligibility gate REJECTS any regular user's live confirm unless they have
   linked keys — their trade never touches the operator account.
5. **Regular users stay manual-confirm.** Leave `AUTO_CONFIRM_THRESHOLD=1.0` for
   them. For **admin** auto-trade, set `AUTO_CONFIRM_THRESHOLD=0.85` and
   `AUTO_CONFIRM_LIVE_ENABLED=true`; auto-confirmed trades run on the operator
   account (`user_id="auto"`) — a regular user's trade is never auto-confirmed.

## Eligibility gate (Phase 5)

`engine.per_user_live_eligibility(user_id)` is consulted in `confirm_trade`
right after the `SIMULATION_MODE` veto:

- Flag **off** → everyone eligible (operator account; unchanged).
- `'auto'`/`''` (admin auto-trade, unattended) → eligible (operator account).
- Operator/admin human user → eligible (operator account; no own keys needed).
- Regular user **with** linked, decryptable keys → eligible (their own account).
- Regular user **without** keys → **REJECTED** (never the operator account).

## Current state (default config)

- `PER_USER_LIVE_ENABLED=false` → no order ever routes to a per-user account;
  the bot trades only the operator account, exactly as before.
- No auto-trade for regular users (threshold stays `1.0`).
- All capability is **built and tested** but **inert**. The whole feature is
  **capability, not activation**: users *can* link accounts and we *can*
  validate + encrypt them, with zero change to how the bot trades today.

## Follow-ups (after enable)

- **Per-user live-balance size-clamp.** The size clamp in `confirm_trade` still
  reads the operator's cached balance; per-user balance caching is a refinement
  for when the feature is turned on with real volume.
