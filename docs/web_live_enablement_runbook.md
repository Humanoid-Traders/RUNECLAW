# Operator runbook — enabling web live trading

Everything below turns the (default-OFF) web live-trading capability on for a
**small allowlist of vetted users**, on **their own exchange keys**, inside a
**revocable Authority Envelope**. RUNECLAW never custodies funds. Read
`docs/web_live_trading.md` and `docs/authority_nl.md` first.

## The five preconditions (all must hold, per user)

A web user reaches a LIVE order only when every one of these is true — the gate
fails closed on any gap:

1. **Operator feature switch** — `WEB_LIVE_TRADING_ENABLED=1` (deployment env).
2. **Bot in live mode** — the process itself is live, not paper/demo.
3. **User opt-in** — the dedicated `web_live_enabled` flag (per user).
4. **Own keys** — the user connected their own exchange credentials.
5. **Enforce-mode envelope** — the user bound an Authority Envelope and set it
   to `enforce`.

## Step-by-step

1. **Vet the user.** Confirm identity/eligibility per your policy. Live web
   trading is opt-in and account-scoped; start with a tiny allowlist.
2. **Set the deployment switch.** `WEB_LIVE_TRADING_ENABLED=1` in the bot's env,
   and `WEB_LIVE_LEDGER_PATH` to a persisted path (the 24h notional spend book;
   defaults to `data/web_live_ledger.json`). Ensure `data/` survives redeploys.
3. **User connects their own keys** (`/connect <venue>` — their account, encrypted
   at rest; RUNECLAW reads/executes, never withdraws).
4. **User authors + arms their envelope** on the web ("Your trading authority"
   card): describe limits in words → Save (shadow) → **Enforce**. Or Telegram
   `/authority`-equivalent. Enforce is a deliberate, separate step.
5. **Flip the user's opt-in.** Operator runs `/weblive web:<id> on` (operator-only).
   `/weblive web:<id>` with no action prints the readiness card — use it to see
   exactly which of the five gates remain.
6. **Verify READY.** `/weblive web:<id>` should show ✅ on all five. Only then can
   that user confirm a live order — and each order is still authorized per-trade
   against their envelope (venue, symbol, per-trade + 24h notional caps).

## Rollback / kill

- **One user:** `/weblive web:<id> off` (revokes the opt-in) — or the user
  revokes their own envelope (kill-switch; authorizes nothing).
- **Everyone, instantly:** unset `WEB_LIVE_TRADING_ENABLED` (or set to `0`). The
  gate's first precondition fails → every web user drops to paper immediately, no
  redeploy of code required.
- **The bot itself:** existing per-user live loss breakers and the global safe-halt
  still apply on top of all of the above.

## Invariants (do not weaken)

- The global switch is env-only; `/weblive` toggles a **single user's** opt-in and
  never the whole capability, and moves no funds.
- `web_live_enabled` is separate from `can_trade_live` (which stays structurally
  False for web ids) — no stale legacy flag can open the web path.
- No live web order exists without an enforce-mode envelope authorizing that
  specific trade. Notional that can't be verified against a cap is denied, not
  waved through.
