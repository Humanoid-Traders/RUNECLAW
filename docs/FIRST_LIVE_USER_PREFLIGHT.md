# RUNECLAW — First Live User Pre-Flight Checklist

Run this **once**, in order, the first time you onboard a real user to live
trading. It de-risks the first real-money trade by verifying every gate is set
the way you intend *before* any size hits the exchange. Stop at the first step
that doesn't behave as described and fix it before continuing.

> Prereq: you've already read `docs/MULTI_USER_LIVE_SETUP.md` and the bot is
> deployed. Keep the per-trade cap tiny for the first user (e.g. $25) and raise
> it only after a clean round-trip.

---

## 0. Operator baseline (before touching users)

- [ ] `/health` — engine running, WS connected, no CRITICAL alerts.
- [ ] `/status` — live mode confirmed; note current rejection reasons.
- [ ] `/accounts` — only the **operator** row shows (no stray per-user accounts yet).
- [ ] Confirm `.env`: `SIMULATION_MODE=false`, `LIVE_TRADING_ENABLED=true`,
      `PER_USER_LIVE_ENABLED=true`. Restart if you changed anything.

## 1. Access — grant exactly the intended scope

- [ ] User has sent `/start` (they show as **pending** in `/users`).
- [ ] Their Telegram ID is in **`LIVE_TRADER_TELEGRAM_IDS`** — **not**
      `ADMIN_TELEGRAM_IDS`. (Admin-listing them defeats per-user isolation and
      grants admin commands.) Restart after editing `.env`.
- [ ] `/approve <id>` → role **trader** (not admin).
- [ ] `/grant_live <id>`.
- [ ] Sanity: in `/users` they are `trader` + LIVE mode, and they do **not**
      appear as an admin.

## 2. Keys — link and verify the user's own account

- [ ] User sends `/connect <key> <secret> <passphrase>` in a **private DM**
      (the message auto-deletes; keys are validated with a read-only balance
      check before anything is stored).
- [ ] User runs `/exchange` → status **connected**, fingerprint shown.
- [ ] User runs `/livebalance` → shows **their** balance (a small real number),
      not the operator's.

## 3. Limits — start the user small

- [ ] `/setcap <id> 25` (or your chosen floor) — caps their per-trade margin.
- [ ] Confirm in `/accounts` the user row now appears with their equity.

## 4. Routing dry-run — prove isolation BEFORE size

- [ ] Have the user run `/scan` (or `/analyze <symbol>`) and get a signal with a
      confirm/reject button — **do not confirm yet**.
- [ ] Verify they get a **manual confirm button**, never an auto-placed trade
      (auto-trade is operator-only).
- [ ] `/whynot <symbol>` (as the user, if a trade was skipped) reads sensibly.

## 5. First real trade — one tiny round-trip

- [ ] User confirms **one** signal. Expected: size ≤ your `/setcap` value, placed
      on **their** account.
- [ ] `/accounts` — the position shows under the **user's** row (exposure ≤ cap),
      operator row unchanged.
- [ ] User `/livepositions` — the position has a **stop-loss and take-profit on
      exchange**. If unprotected past the grace window you'll get a CRITICAL
      alert; do not proceed until SL/TP are confirmed on-exchange.
- [ ] Let it close (or close it: user `/liveclose <id>`). Confirm PnL lands in
      **their** account and their streak/breaker — not the operator's.

## 6. Kill-switch rehearsal — know the brakes work

- [ ] With the position open (or a fresh tiny one), trigger **Emergency Stop**
      (admin) and CONFIRM. Expected: every account's breaker trips, queued ideas
      clear, and **all** open positions (operator + this user) are flattened —
      the summary lists the per-user account.
- [ ] `/reset` (resume) — clears the breakers on **all** engines.
- [ ] `/accounts` — confirm flat and un-halted.

## 7. Go / no-go

Proceed to wider onboarding only if **all** held:
- right account (their balance, their positions, their PnL),
- right size (≤ cap, ≤ global micro cap),
- SL/TP on exchange,
- isolation (their breaker, not the operator's),
- kill-switch flattened them too.

If any step surprised you, stop and reconcile it against
`docs/MULTI_USER_LIVE_SETUP.md` before raising caps or adding users.

---

### Quick command reference
| Command | Who | Purpose |
|---|---|---|
| `/health` `/status` | admin | engine vitals / rejections |
| `/accounts` | admin | per-account equity, exposure, breaker |
| `/approve` `/grant_live` `/setcap` | admin | access + live + per-trade cap |
| `/connect` `/exchange` `/livebalance` | user | link + verify own account |
| `/scan` `/analyze` `/whynot` | user | signals + why-skipped |
| `/livepositions` `/liveclose` | user | open trades + close |
| Emergency Stop / `/reset` | admin | flatten-all + halt / resume |
