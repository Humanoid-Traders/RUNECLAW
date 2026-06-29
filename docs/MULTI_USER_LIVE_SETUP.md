# RUNECLAW — Multi-User Live Trading Setup

How to let regular users trade live **on their own Bitget accounts** (manual
confirm only; no auto-trade for non-admins), with per-user risk isolation and
own-equity sizing actually in force.

> ⚠️ **Use `LIVE_TRADER_TELEGRAM_IDS`, not `ADMIN_TELEGRAM_IDS`, for regular
> users.** Adding a user to `ADMIN_TELEGRAM_IDS` makes them a full admin **and**
> marks them an *operator* — which routes their trades to the **operator's**
> account on the **shared** risk engine, defeating per-user isolation (#122) and
> own-equity sizing (#124). The dedicated live-trader allowlist avoids both.

---

## The two allowlists (important)

| Env var | Grants | Operator/admin? |
|---|---|---|
| `TELEGRAM_CHAT_ID` | the operator account itself | **Yes** — operator |
| `ADMIN_TELEGRAM_IDS` | admin commands + premium routing + auto-trade | **Yes** — admin |
| `LIVE_TRADER_TELEGRAM_IDS` | use the bot + trade live on **own** account | **No** — regular user |

All three let a user *onto* the bot and the live-trade allowlist. Only the first
two confer operator/admin identity. For per-user live trading you want regular
users in **`LIVE_TRADER_TELEGRAM_IDS`**.

---

## `.env` — minimum changes

```env
# already set for live
SIMULATION_MODE=false
LIVE_TRADING_ENABLED=true

# enable per-user live (route each user to THEIR own account)
PER_USER_LIVE_ENABLED=true

# the regular users allowed to trade live on their own accounts
LIVE_TRADER_TELEGRAM_IDS=11111111,22222222

# recommended hardening for real money (default OFF)
LIVE_RISK_HARDENING_ENABLED=true
REGIME_HARD_GATES_ENABLED=true
```

Restart the bot after editing `.env`.

---

## Per-user onboarding flow

```
1. User sends /start
   → auto-registers as PENDING (NOT yet authorized)

2. Operator adds the user's Telegram ID to LIVE_TRADER_TELEGRAM_IDS in .env
   → restart the bot

3. Operator runs  /approve <telegram_id>          (role: trader)
4. Operator runs  /grant_live <telegram_id>        (enables live permission)

5. User sends  /connect <api_key> <api_secret> <passphrase>  in a private DM
   → message auto-deleted, keys Fernet-encrypted at rest

6. User runs  /exchange   to verify the connection

7. User is LIVE on their OWN account: /scan, confirm trades, /livepositions
```

Both gates must pass for a live order: the env allowlist **and** the per-user
`/grant_live` flag (`_can_trade_live`). A user not on the allowlist can never
trade live even if the stored flag says otherwise.

---

## What each regular live user gets

- **Own account execution** — confirmed trades execute on their linked Bitget
  account, never the operator's.
- **Own-equity sizing** — position size is computed from *their* balance, not the
  operator's (#124).
- **Own risk breakers** — their loss streak / circuit breaker / daily-loss /
  drawdown are isolated; one user's halt never stops anyone else (#122).
- **Manual confirm only** — auto-trade runs only on the operator scan path;
  regular users always get the confirm/reject button.

## What regular users CANNOT do (admin only)

`/halt`, `/closeall`, `/emergency_stop`, `/approve`, `/grant_live`,
`/grant`/`/revoke`, `/mode`, and admin auto-trade. The global kill-switch
(emergency stop / `/closeall`) still flattens **every** account including theirs.

## Admin visibility & limits

- `/accounts` — live risk per account (equity, open positions, exposure,
  breaker state) across the operator and every per-user account.
- `/setcap <telegram_id> <max_margin_usd | off>` — cap how much margin a user may
  commit to a single live trade. Tighten-only: it can only *reduce* the size the
  risk engine already sized, never raise it above the global live cap
  (`MICRO_MAX_POSITION_USD`). Use it to start a new/untrusted user small (e.g.
  `/setcap 12345678 25`) and raise as they earn trust; `off` clears it.

---

## Safety properties (now actually delivered)

- **Operator keys never exposed** — users only ever see their own account.
- **User keys encrypted at rest** — Fernet; set `EXCHANGE_SECRET_KEY` in prod.
- **No silent cross-account trades** — with `PER_USER_LIVE_ENABLED=true`, a user
  without linked keys is **rejected**, never placed on the operator account.
- **Per-user circuit breakers + equity sizing** — in force for users onboarded
  via `LIVE_TRADER_TELEGRAM_IDS` (because they are not operators).
- **Auto-trade is admin-only.**
