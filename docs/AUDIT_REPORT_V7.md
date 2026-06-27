# RUNECLAW — Audit Report V7 (Final / Post-CI-Hardening)

**Date:** 2026-06-27
**Scope:** Full risk-critical surface of the LIVE-ONLY trading bot — risk engine, live executor, engine confirm/execute path, compliance gates, credential/config/Telegram security.
**Baseline:** Audited at `main` @ `ceffb5d` (after PR #20 merged: CI wired, test drift reconciled 42→1, stale-data guard restored to 300s).
**Method:** Four independent deep-read audits (one per domain), each finding then **re-verified by hand** against the source (repro scripts / direct file:line reads). Findings below are confirmed, not speculative. CI gate is green; full `ruff` (beyond the CI-gated E9/F821) reports 625 style issues (327 auto-fixable, non-gating).

This report supersedes nothing — it is additive to V4–V6.1, which closed the `RC-AUD-*`, `C2-*`, and `F-*` series. The codebase shows extensive prior hardening; what follows is what **remains**.

---

## Executive summary

| # | Severity | Title | Area | Status |
|---|----------|-------|------|--------|
| F-1 | **CRITICAL** | Blocked live trades reported as SUCCESS (prefix-match gap) | engine↔executor | Verified |
| F-2 | **CRITICAL** | Open self-registration: `/start` → authorized `trader` | telegram/authz | Verified |
| F-3 | **HIGH** | Notional/margin unit mismatch — risk checks evaluate ~1/leverage of the real order | risk↔executor | Verified (design) |
| F-4 | **HIGH** | Emergency/adopted positions unprotected for the full 90s grace window | executor | Verified |
| F-5 | **HIGH** | `CONFIG.exchange.leverage` does not exist → limit-order adoption always throws (silently) | executor | Verified |
| F-6 | **HIGH** | NaN/inf `entry_price`/SL/TP bypasses validator + most risk checks | risk/models | Verified |
| F-7 | **HIGH** | Future-dated timestamp defeats the stale-data guard (negative age) | risk | Verified |
| F-8 | **HIGH** | Self-minted "human approval" (Lock 5) → unattended live execution once env-armed | engine/compliance | Verified |
| F-9 | MEDIUM | v3 SL/TP reports success on garbage response; stores sentinel order id `"v3-strategy"` | executor | Verified |
| F-10 | MEDIUM | POST_ONLY retry regenerates `clientOid` → defeats venue dedup (double-fill risk) | executor | Verified |
| F-11 | MEDIUM | Destructive Telegram callbacks (emergency-stop/pause/mode) gate on auth only, not role | telegram/authz | Verified |
| F-12 | MEDIUM | `/trade` and `/setllm` reachable by any auto-approved user (authz path inconsistency) | telegram/authz | Verified |
| F-13 | MEDIUM | Critique gate fails OPEN on exception in LIVE mode | engine | Verified |
| F-14 | MEDIUM | SIMULATION veto runs AFTER an exchange-mutating SL→breakeven side-effect | engine | Verified |
| F-15 | LOW | Misc: raw exception text to chat; unbounded consent ledger; tracked runtime log; red-team gaps; stale "18-check" docstring | various | Verified |

**The two roots to fix first:** F-2 (open registration is what makes F-11/F-12 reachable by strangers) and F-1 (phantom-fill records corrupt the audit chain + learning data). F-3 is the highest-stakes *correctness* question but is a design-intent decision, not a mechanical bug.

---

## CRITICAL

### F-1 — Blocked live trades are recorded as successful fills
**`bot/core/engine.py:1701-1704`** (consumer) vs **`bot/core/live_executor.py:1417,1529,1564,1569`** (producer)

The engine decides whether a live order succeeded by prefix-matching the executor's return string:

```python
live_failed = any(result.startswith(prefix) for prefix in (
    "EXECUTION FAILED:", "INSUFFICIENT FUNDS:", "INVALID ORDER:",
    "BLOCKED:", "PREFLIGHT FAILED:"))
```

But the executor returns these **block** strings that match none of the prefixes (note `"EXECUTION BLOCKED:"` does **not** start with `"BLOCKED:"`):
- `live_executor.py:1417` → `"REFUSED: position persistence is broken …"`
- `:1529` → `"Live execution blocked: live mode was deactivated …"`
- `:1564` → `"EXECUTION BLOCKED: system is in degraded mode (paused) …"`
- `:1569` → `"EXECUTION BLOCKED: system is in reduce-only mode …"`

**Verified.** When the order was never placed, `live_failed` is `False`, so the engine seals `DecisionRecord(outcome="EXECUTED_LIVE", is_paper=False)` (`engine.py:1728`), logs `decision="TRADE_ACCEPTED_LIVE"` to the learning system (`:1744`), and pops the pending idea so it can never be retried. The tamper-evident audit chain, the learning dataset, and position accounting all record a phantom live trade that does not exist on the exchange.

**Fix (safe, recommended now):** Stop trusting prefixes. Have `execute()` return a structured result (`ExecResult(placed: bool, order_id, message)`) or raise typed exceptions, and gate on `placed`. Immediate mitigation: treat the result as success **only** if it carries a real fill marker (order id / `🟢/🔴 LIVE` / `LIMIT ORDER`), and everything else as failure — fail-closed.

### F-2 — Open self-registration grants `trader` authority to any Telegram user
**`bot/utils/user_store.py:193-209`** (`register`), reached from **`bot/skills/telegram_handler.py:1251`** (`_cmd_start`)

New users are created `"authorized": True, "role": "trader"` on first `/start`, with **no dispatcher-level chat allowlist**. The `trader` role (`user_store.py:31-36`) carries `trade, halt, reset, mode, run, optimize`. The welcome/`pending` UX text claims users must "wait for approval," but the code authorizes immediately — a false sense of safety.

**Verified** (`authorized: True, can_trade_live: False` at `user_store.py:199-200`). **Mitigant:** live *execution* is separately gated — `can_trade_live` defaults to `False`/admin-only (`user_store.py:263-277`, enforced at `telegram_handler.py:6212-6222`). So a stranger **cannot place live orders**, but **can** `/halt` (circuit-break the live bot), `/reset`, `/mode aggressive`, press **emergency-stop** (F-11), and queue paper trades. For a live-money bot that is a real integrity/DoS exposure.

**Fix (behavior change — confirm before applying):** enforce a hard `TELEGRAM_CHAT_ID` allowlist at the top of the auth guard so only allowlisted IDs reach any privileged command, **or** default new users to `pending`/`authorized: False` requiring admin `/approve`. ⚠️ Defaulting to `pending` will lock out anyone not yet seeded — make `seed_admin(CONFIG.telegram.chat_id)` the bootstrap path first so the operator isn't locked out.

---

## HIGH

### F-3 — Notional vs margin: the risk engine validates ~1/leverage of the order it actually places
**`bot/risk/risk_engine.py:457,476`** (producer) vs **`bot/core/live_executor.py:1678`** (consumer)

The risk engine sizes and caps `position_usd` as **notional** — `position_usd = sizing_equity * max_position_pct/100`, with the comment "The notional cap (20%) is enforced by check #2" — and every exposure/VaR check (#2 POSITION_SIZE, #14 PORTFOLIO_EXPOSURE, #15 SYMBOL_EXPOSURE, #21 VaR) evaluates that figure. In LIVE mode it is clamped to `MICRO_MAX_POSITION_USD = 100`.

The executor consumes the same number as **margin**: `quantity = (size_usd * leverage_mult) / current_price` (`leverage_mult = default_leverage = 5`), and the executor header (`live_executor.py:86-87`) documents "$100 margin … = $500 notional" — i.e. the executor authors *intended* margin semantics.

**Verified inconsistency.** The two sides disagree on what the number means: the engine's protective checks reason about a $100 notional while the executor places ~$500 notional (and dynamic leverage at `:1662` can push it higher in low-ATR regimes). This is a **design-intent reconciliation**, not a one-line bug — the two agents that found it disagreed on whether the 5× is intended.

**Recommendation (do NOT auto-fix — your decision):** Pick one unit and make it explicit end-to-end. Either (a) engine returns margin and documents `position_size_usd` as margin, with checks multiplying by the validated leverage for exposure/VaR; or (b) executor treats `size_usd` as notional (`quantity = size_usd / price`, margin = notional/leverage for the balance check). Then add an assertion at the executor boundary: `notional ≤ equity * max_symbol_exposure_pct/100`, and have the executor use the exact leverage the engine validated.

### F-4 — Emergency/adopted positions have no stop for the full 90-second grace window
**`bot/core/live_executor.py:3073-3102`** (grace skip), `2424-2475` (emergency position), `1144-1225` (adopted/unprotected)

`check_positions()` skips **all** local SL/TP price monitoring for the first 90s after `opened_at` (`:3078 continue`). The happy path guarantees an exchange stop before returning, but two paths create an `open` position with `sl_order_id=None` and a fresh `opened_at=now`: the post-order-crash "emergency position" (`:2424`, best-effort SL may fail at `:2458`) and adopted orphans whose safety-SL placement failed (`:1208 unprotected=True`). For those, the 90s grace means **neither an exchange stop nor local monitoring** — fully unprotected on a leveraged perp.

**Fix (safe):** apply the grace skip only when a stop actually exists/is in-flight (`pos.sl_order_id` set). If `sl_order_id is None`, run local SL monitoring immediately, or force-flatten an emergency/adopted position still stop-less after the first failed placement.

### F-5 — `CONFIG.exchange.leverage` does not exist → limit-order adoption silently dead
**`bot/core/live_executor.py:1349`** — `leverage = CONFIG.exchange.leverage or 10`

**Verified:** `CONFIG.exchange` has `default_leverage` (=5), no `leverage` → `AttributeError`, swallowed by the broad `except Exception` (`:1395`, debug-level only). So `adopt_exchange_limit_orders()` throws before adopting anything: orphaned limit orders (placed-but-not-tracked, the timed-out-submission failure mode) are never re-tracked — they sit on the exchange with no expiry/fill-handling/SL-attach, invisibly.

**Fix (safe, trivial):** `CONFIG.exchange.default_leverage`. Add a unit test exercising `adopt_exchange_limit_orders` against a mocked open order, and raise the swallow level so config typos surface.

### F-6 — NaN/inf prices bypass the model validator and most risk checks
**`bot/utils/models.py:101`** + **`risk_engine.py:758,768`** (#10/#11)

**Verified:** `TradeIdea(entry_price=nan, stop_loss=nan, take_profit=nan, …)` is accepted — `nan <= 0` is `False` and both directional comparisons are `False`, so validation passes. In `evaluate`, ENTRY_PRICE (#10) and STOP_LOSS (#11) report "valid"; only the volatility guard (#16, `elif idea.entry_price > 0`) incidentally rejects. The fail-closed contract rests on a single incidental check.

**Fix (safe, fail-closed):** add `math.isfinite()` guards in the `TradeIdea` validator for entry/SL/TP and make #10/#11 reject non-finite explicitly.

### F-7 — Future-dated timestamp defeats the stale-data guard
**`risk_engine.py:784-788`** (#12)

**Verified:** `data_age = (now - idea.timestamp).total_seconds()`; a future timestamp yields negative age, never `> max_age`. A 5-hour-future idea passes as `STALE_DATA: -18000s old OK`. Clock skew or a crafted/replayed forward-dated idea bypasses staleness entirely.

**Fix (safe, fail-closed):** also reject `data_age < -CLOCK_SKEW_TOLERANCE` (a few seconds tolerated; hours not).

### F-8 — Self-minted Lock 5 enables unattended live execution once env-armed
**`bot/core/engine.py:1562-1587`** (auto-mint), `779-796` (auto-confirm live), `compliance_engine.py:138-157`

The "human approval" lock (Lock 5) is **minted by the engine itself** (`issue_approval_token`) every time it reaches the compliance gate in live mode, then immediately consumed — `authorize` cannot distinguish a real confirmation from an auto-minted one. With `LIVE_TRADING_ENABLED=true`, `SIMULATION_MODE=false`, `AUTO_CONFIRM_LIVE_ENABLED=true`, and a lowered `AUTO_CONFIRM_THRESHOLD`, real orders are placed with `user_id="auto"` and **zero human action**; a startup/`:1572` WARNING is logged but is non-blocking. Additionally, `ExecutePaperTradeSkill.execute` (`skill_registry.py:710`) calls `confirm_trade(trade_id)` with **no `user_id`** (`""`), a non-button path that still satisfies the auto-minted token.

Fail-closed by default (threshold `1.0`, flags off) → HIGH not CRITICAL. **Fix (judgment call):** do not auto-mint Lock 5 for `user_id in ("","auto")` — require a real callback-sourced token; or require a second explicit opt-in + a hard notional cap for unattended live.

---

## MEDIUM

- **F-9 — `live_executor.py:2918-2920`:** on Bitget `code=="00000"` with an empty/renamed data block, the v3 SL/TP order id defaults to the literal `"v3-strategy"` and is stored as both SL and TP id. The position is then treated as protected (truthy `sl_id` → RC-AUD-001 flatten skipped) though no usable trigger id exists, and `cancel_order("v3-strategy")` fails on close. **Fix:** missing real id ⇒ treat as failure (return `None`, retry/flatten); never use a placeholder as an order id.
- **F-10 — `live_executor.py:1992-1996`:** the POST_ONLY retry uses `coid + "-r1"`, so the venue can't dedup the retry against the original. If the "original absent" check is wrong (stale `fetch_open_orders`), the retry opens a **second** real position. **Fix:** keep the same `clientOid` on retry; only suffix after a positive, freshly-fetched terminal-non-filled confirmation.
- **F-11 — `telegram_handler.py:5350,5425,5441,5478`:** inline-keyboard callbacks gate on `_check_auth` (any authorized user) only — so any auto-approved user (F-2) can press **emergency-stop** (closes all live positions), pause, or switch mode. The `confirm:`/`reject:` callbacks *are* properly hardened (IDOR + live check); the menu/risk ones are not. **Fix:** add a role/permission check before destructive callback branches.
- **F-12 — `telegram_handler.py:3681-3693` (`/trade`) and `:3252-3284` (`/setllm`):** `/trade` does an inline `authorized`-only check, skipping `_guard("trade")` (role + F-14 24h session-staleness); `/setllm` is gated on the `mode` permission (in the `trader` set), letting any auto-approved user swap the analysis LLM / inject a key over chat. **Fix:** route `/trade` through `_guard("trade")`; gate `/setllm`,`/llmreset` behind admin.
- **F-13 — `engine.py:1491-1552`:** the adversarial critique gate's `try/except` fails **open** (logs, proceeds) in LIVE mode — a malformed idea/snapshot that crashes critique silently disables the strongest discretionary brake. Defensible only because the hard risk/compliance/sim gates remain fail-closed around it. **Fix:** in LIVE mode, a critique *exception* should fail-closed (reject); narrow the `except` to specific exceptions so logic bugs surface.
- **F-14 — `engine.py:1605 vs 1683`:** the FSM transitions to `EXECUTING` and performs a pyramid SL→breakeven **exchange mutation** (`:1619-1634 _update_exchange_sl`) *before* the SIMULATION_MODE hard veto at `:1683`. A "vetoed" confirm can still have modified a different live position's stop on the exchange. **Fix:** move the sim veto (and the gate block) above the `EXECUTING` transition and any exchange-mutating side-effect.

---

## LOW / informational (F-15)

- **`telegram_handler.py` (many sites, e.g. `:2091,2118,…`):** raw `str(exc)` sent to chat doesn't pass through the log redactor — a credential-bearing ccxt/auth error could reach the chat unredacted. Route user-facing errors through `_redact_string` or send generic text + log detail.
- **`compliance_engine.py:97,259,335`:** `_consent_ledger` is an unbounded append-only list (memory growth over a long-running live process). Cap/rotate or persist-and-truncate. (The engine `state_history` *is* capped; this isn't.)
- **`logs/audit_chain.jsonl` is tracked in git** and is rewritten by every test/run, producing spurious diffs (hit repeatedly this session). Move runtime logs out of version control (`.gitignore`) and seed an empty chain at runtime.
- **Stale dynamic-leverage / equity-cache:** live equity used in re-check sizing can be up to 5 min stale (logged, not failed-closed). Consider failing closed past a hard cache-age threshold.
- **Red-team coverage gaps (`bot/core/red_team.py`):** no scenarios for NaN/inf (F-6), future-dated timestamp (F-7), notional-correctness (F-3), or a VaR breach (#21 never exercised); header still says "18-check" though the engine has 23. Add scenarios 1–4 at minimum.
- **Requirements hygiene:** `requirements.txt`/`requirements-ci.txt` mix `==` and `>=` pins while `requirements.lock` pins exactly — keep the lock authoritative for production installs.
- **Lint debt:** full `ruff` reports 625 issues (327 auto-fixable, mostly unused imports); CI only gates E9/F821. Consider a scheduled `ruff --fix` sweep.

---

## What is clean (verified)

- **No committed secrets / credentials**; `.env`, `users.json`, `data/` all gitignored; `.env.example` ships safe defaults (`SIMULATION_MODE=true`, `BITGET_SANDBOX=true`, `LIVE_TRADING_ENABLED=false`).
- **No injection surface** in `bot/` (no `eval`/`exec`/`pickle`/`shell=True`/`yaml.load`); manual-trade input regex-validated; symbols restricted to `[A-Z0-9]{1,15}`.
- **Secret logging:** all log output runs through key-name + inline-value redaction with a tamper-evident hash chain; `/llmstatus` shows only a key fingerprint.
- **Config bounds:** `_env_float_bounded` clamps risk limits and rejects inf/nan; empty safety-switch values fall back to the safe default; `is_live()` fails closed without a configured `TELEGRAM_CHAT_ID`; `simulation_mode` is an independent hard veto.
- **Dashboard/JWT auth:** dashboard refuses to start without `DASHBOARD_API_KEY` (`hmac.compare_digest`); `auth_routes` refuses a missing/default `JWT_SECRET`, has lockout + token revocation.
- **The SIMULATION_MODE hard veto and the pre-execution risk re-check are correctly wired and fail-closed** on the real path; state persistence fails closed on corrupt files; single execution funnel (`execute()` reachable only via `confirm_trade`).

---

## Recommended remediation order

1. **F-1** (phantom-fill detection) and **F-5** (leverage typo) — safe, mechanical, high value. Fix now.
2. **F-6 / F-7** (NaN + future-timestamp guards) — safe, fail-closed direction. Fix now.
3. **F-4** (grace-window protection) and **F-14** (sim-veto ordering) — safe, contained. Fix now.
4. **F-2 / F-11 / F-12** (authz) — fix together; F-2 needs an allowlist/bootstrap decision to avoid operator lockout.
5. **F-8 / F-13** (human-gate + critique fail-open) — policy decisions; pick the live-mode posture.
6. **F-3** (notional/margin units) — **design decision required** before any code change; highest correctness stakes.
7. **F-9 / F-10** (executor robustness) and **F-15** (LOW hygiene) — schedule.

Each fix should land with a regression test and pass the CI baseline gate.
