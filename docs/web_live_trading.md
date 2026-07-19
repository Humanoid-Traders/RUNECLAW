# Web live trading — envelope-gated self-serve (staged)

## The expansion blocker (why this exists)

Today a pure website signup can only **paper** trade. Live execution is gated on
a linked Telegram identity **and** the operator allowlist (`_can_trade_live` /
`UserStore.can_trade_live` both hard-return `False` for `web:<id>` accounts).
That is safe, but it is the hard ceiling on web-user expansion: a web user can
never trade their own account live, no matter how many exchange keys they've
connected.

The chosen unblock (self-serve, on the user's own keys, thesis-aligned): a web
user may open a **live** trade on **their own connected exchange**, but only
inside a revocable **Authority Envelope** they set. RUNECLAW custodies nothing —
the trade rides the user's own API keys, and the envelope authorizes and caps
every order. *The AI proposes, the envelope authorizes, the recorder proves.*

## The gate (`bot/web/web_live_gate.py`)

A single, pure, **fail-closed** decision. A web user reaches LIVE only when
ALL five preconditions hold:

| # | precondition          | source                                    | default |
|---|-----------------------|-------------------------------------------|---------|
| 1 | `feature_enabled`     | env `WEB_LIVE_TRADING_ENABLED`            | **OFF** |
| 2 | `bot_is_live`         | `CONFIG.is_live()`                        | —       |
| 3 | `user_opted_in`       | `UserStore.web_live_enabled(id)` (new flag) | **OFF** |
| 4 | `has_own_keys`        | credential store `has(id)`                | —       |
| 5 | `envelope_enforcing`  | a bound Authority Envelope in enforce mode | **none** |

Any unmet precondition → **paper**, with a reason naming the first gap so the UI
can guide the user ("connect your own exchange keys", "set an Authority Envelope
in enforce mode", …). By default (feature flag off) behaviour is byte-identical
to today: web = paper. The dedicated `web_live_enabled` flag is **separate** from
`can_trade_live` (which stays structurally `False` for web ids), so no stale
legacy flag can ever open this path.

## What this PR ships (Stage 1 — foundation, default OFF)

- The pure gate + the dedicated `web_live_enabled` store flag (web-only).
- Wiring in the gateway: `_trade_mode` and `handle_trade_confirm` consult the
  gate for web ids instead of a blanket paper-lock. Confirm returns the reason +
  a per-precondition checklist so the web UI can show exactly what's left.
- The Trade-view confirm modal surfaces "🔓 to trade live on your own account: …".

Because every default is OFF/none, **no live web order is possible yet** — this
is the safe decision spine everything else hangs off.

## Stages (status)

2. **Per-user Authority Envelope binding — DONE** (say-it-in-words NL compiler +
   per-user store + web card; `_web_envelope_enforcing` reads the store).
3. **Per-trade envelope authorization at confirm — DONE.** When the gate passes,
   `handle_trade_confirm` calls `_authorize_web_live_trade`: it reconstructs the
   order (venue from the user's active keys, market_type=swap, symbol, notional =
   manual margin × configured leverage) and runs `authority.authorize` against the
   bound enforce-mode envelope with the 24h spend from a per-user
   `AuthoritySpendLedger`. FAIL-CLOSED — a symbol/venue/notional/daily-cap
   violation, or an auto-sized order whose notional can't be verified against a
   cap, returns `authority_denied`. Only an allow records the spend and proceeds
   to `engine.confirm_trade(user_id=…)`, which routes to the user's own-keys
   executor (per-user loss breakers apply).
4. **Operator enablement runbook** (still ahead) — set `WEB_LIVE_TRADING_ENABLED=1`,
   per-user `web_live_enabled`, require an enforce-mode envelope. Roll out to a
   small allowlist first. `WEB_LIVE_LEDGER_PATH` persists the 24h spend book.

## Architecture note — is the bot a blocker?

The web→gateway→bot boundary is sound (JWT at the web edge, secret-authed
service channel, one risk gate). The blocker was never the boundary — it was the
*operator-allowlist + Telegram-linked-identity requirement for live*. This gate
removes that requirement for web users **without weakening any existing check**:
Telegram users keep their exact path; web users get a new, separately-flagged,
envelope-bound path. The single bot process remains the execution hub (fine to
hundreds of users; a horizontal-scale concern only at much larger scale, tracked
separately).
