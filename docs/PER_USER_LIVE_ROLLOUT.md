# Per-user live trading — staged rollout runbook

Enabling per-user live trading lets each user place **real-money** orders with
their **own** linked Bitget keys, on both the Telegram bot and the website. This
is deliberately staged behind an **allowlist** so the blast radius is contained:
you approve users one at a time and everyone else stays paper-only.

## The three gates (all must pass before any real order goes out)

1. **Master switch** — `PER_USER_LIVE_ENABLED` (env). Default **off** → operator
   account only, exactly as today. Nothing per-user goes live until you set it.
2. **Live allowlist** — even with the switch on, a *regular* user's live trade is
   rejected unless an admin has approved them (`/grant_live <telegram_id>`).
   Enforced at execution in `engine.per_user_live_eligibility` (fail-closed: if
   the user store is unavailable, the trade is denied). Operators/admins are
   always allowed and trade the operator account.
3. **Linked keys** — the user must have `/connect`ed their own valid exchange
   keys (encrypted at rest). Without them their live trade is rejected, never
   silently placed on the operator account.

### Connecting a venue (Bitget or Hyperliquid)

Per-user connect is **multi-venue**. A user links exactly one venue at a time;
their live executor is built for that venue.

- **Bitget** — Telegram `/connect <api_key> <api_secret> <passphrase>`, or the
  web Account → Exchange keys form (venue = Bitget). Needs USDT-M futures
  read + trade permission; withdrawals disabled.
- **Hyperliquid** — Telegram `/connect hyperliquid <wallet_address>
  <agent_private_key>`, or the web form (venue = Hyperliquid). Use an **API
  (agent) wallet** key, never the main wallet key.

Both paths read-only-validate the keys (a balance probe) before storing, and the
web submission is AES-256-GCM encrypted, pulled by the bot over the shared-secret
channel, and re-stored in the bot's Fernet store keyed by Telegram id. The venue
travels with the record end-to-end, so a Hyperliquid-connected user's executor
builds a `ccxt.hyperliquid` client from their own wallet. Re-connecting a
different venue rebuilds the executor on the new venue.

The website trade path (`app/routes/webtrade.js` → bot gateway → `_can_trade_live`)
routes through the **same** gate, so the bot and the web behave identically.

## Per-user safety that's always on (independent of the switch)

- **Per-user loss breakers** wired to each user's own realized PnL (C1).
- **Executing-account size clamp** — sizing is clamped to *that user's* free
  margin, never the operator's (C2).
- **Per-user margin cap** — operator-set ceiling per user (`/setcap`),
  applied to auto trades **and** manual `/trade` overrides (C5).
- **Per-user kill/breaker re-check** at confirm time (runs against the engine
  that owns that user's risk state).

## Go-live procedure

1. Merge + deploy this build. Confirm the engine authenticates
   (`Credential preflight OK`) and paper flows are healthy.
2. Set `PER_USER_LIVE_ENABLED=true` in the bot's environment and restart.
   (Operators keep trading exactly as before; no regular user is live yet.)
3. Approve **yourself first**: `/grant_live <your_telegram_id>`, `/connect` your
   keys, then place **one small real order** end-to-end on both the bot and the
   website. Confirm: order fills on YOUR account, equity/positions reflect it,
   SL/TP placed, and `/livepositions` matches the exchange.
4. Optionally set a per-user ceiling before approving others: `/setcap <id> <usd>`.
5. Approve users as you vet them: `/grant_live <id>`. Revoke any time:
   `/revoke_live <id>` (they fall back to paper immediately).

## Rollback

- Instant, per user: `/revoke_live <id>`.
- Global: set `PER_USER_LIVE_ENABLED=false` and restart → back to operator-only.
Neither touches existing positions; monitoring/reconciliation keeps protecting
every open position across all accounts regardless.

## What is intentionally NOT changed

`PER_USER_LIVE_ENABLED` stays **off by default in the repo** — enabling live
trading is an operator env decision, never a code default, so no deployment goes
live-for-all-users by accident.
