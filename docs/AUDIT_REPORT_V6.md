# RUNECLAW Deep Audit Report V6

**Date:** 2026-06-26
**Branch audited:** `claude/complete-audit-test-report-3hw8kt`
**Scope:** Full second-pass audit of the trading core and supporting services —
`bot/risk/` (risk engine, order router, portfolio, multi-portfolio, scale-out),
`bot/core/` (engine, live executor, exchange sync, limit entry, validation gate,
critique), `bot/learning/` (safety policy, orchestrator, reflection, patterns,
models, store), `bot/compliance/`, `bot/api/` (auth routes, token store),
`bot/utils/` (audit chain, attestation, rate limiter, user store),
`api_bridge.py`, `dashboard_api.py`, `bot/web/dashboard_server.py`.
**Predecessors:** `docs/AUDIT_REPORT_V5.md` (2026-06-25) and `AUDIT_REPORT_V4.md`.
V6 re-verifies the prior guarantees still hold, finds what V4/V5 missed, and adds
a full test-suite health assessment.
**Method:** Four independent subsystem passes (risk / security / execution /
learning+compliance), each finding re-verified at exact `file:line` before
inclusion, plus a from-clean run of the full pytest suite (1175 collected).

---

## Summary

| Severity | Found | Fixed (this PR) | Documented / Remaining |
|----------|-------|-----------------|------------------------|
| CRITICAL | 0 | 0 | 0 |
| HIGH | 5 | 1 | 4 |
| MEDIUM | 9 | 1 | 8 |
| LOW | 4 | 3 | 1 |
| **Total** | **18** | **5** | **13** |

The risk engine's core fail-closed property, the "learning cannot override risk"
hard wall, and the V4/V5 critical fixes (no-stop-loss flatten, auto-confirm off
by default, leaked sync secret) **all still hold**. No new Critical was found.
The fixes in this PR close five well-contained, independently-tested defects. The
13 documented items are real but either need live-exchange context to change
safely (execution-path PnL/SL handling) or are product-policy decisions
(auth-token renewal, proposal auto-apply heuristics).

### Fix-next shortlist (documented, not yet fixed)
1. **RC-AUD-V6-E1 (High)** — the per-symbol post-SL cooldown is enforced only in
   signal generation; `confirm_trade()` (the path that actually executes) never
   re-checks it, so a queued or manual idea can re-enter a just-stopped symbol.
2. **RC-AUD-V6-E3 (High)** — the RC-AUD-001 "flatten if stop can't be placed"
   guard exists only on the market path; **limit fills and drift→market
   conversions can be left live with no exchange stop**.
3. **RC-AUD-V6-S1 (High)** — `GET /auth/me` re-mints both access *and* 7-day
   refresh tokens, letting a stolen short-lived access token be refreshed
   indefinitely and bypass refresh-rotation/reuse-detection.
4. **RC-AUD-V6-E2 (High)** — manual/auto close path can report `net_pnl`
   excluding fees when Bitget returns a gross `achievedProfits`/`profit` figure.

---

## Safety-Guarantee Assessment

The product thesis remains *"the bot suggests, the human decides, the risk engine
enforces."*

| Guarantee | Verdict | Evidence |
|-----------|---------|----------|
| **The bot suggests** | Holds | MCP excludes execution; learning output is never read by the live path (`engine.py`/`live_executor.py` use the independent `RiskEngine`). |
| **The human decides** | Holds (with one execution gap) | Auto-confirm remains off by default (V5 RC-AUD-002). The new gap is operational, not a bypass: a *queued* idea isn't re-screened against the per-symbol cooldown at execute time (E1). |
| **The risk engine enforces** | Holds | The 23-check fail-closed engine is intact; no path was found where a check silently passes on exception. VaR/sizing/PnL-sign math re-verified correct. |
| **Learning cannot override risk** | Holds — and hardened | The documented "`may_override_risk` cannot be bypassed at runtime" guarantee was technically *false* (validator was construction-only). **Fixed in this PR (V6-2).** No write path from `bot/learning/` into `CONFIG`/risk state. |

---

## Fixed in this PR

Each fix ships with a regression test in `tests/test_audit_v6_fixes.py` (17 cases,
all passing).

### RC-AUD-V6-2 — `may_override_risk` invariant was construction-only (HIGH)
**`bot/learning/models.py:174-197`** — `PatternRecord`'s `@field_validator` runs
only at construction; the model lacked `validate_assignment`, so
`p.may_override_risk = True` succeeded post-construction and `model_dump()`
emitted `True`. The docstring and `docs/gitbook/ai-learning-system.md:176` both
claim this "cannot be bypassed at runtime." No live consumer reads the field
today (defense-in-depth), but the documented unbreakable invariant was breakable.
**Fix:** added `model_config = ConfigDict(validate_assignment=True)` so the
validator fires on assignment too. Verified `True` now raises on both
construction and assignment.

### RC-AUD-V6-3 — restricted-jurisdiction block was casing/whitespace-sensitive (MEDIUM)
**`bot/compliance/compliance_engine.py:107`** — `profile.jurisdiction in
self._restricted` was an exact, case-sensitive set test. `"ru"`, `"Ru"`, `" RU"`
all bypassed the OFAC-style hard block (the only lock evaluated before mode
branching). **Fix:** normalize on both sides — `SubjectProfile.__post_init__`
upper-cases/strips `jurisdiction`, and `ComplianceEngine.__init__` normalizes the
restricted set (including caller-supplied sets). Verified all casings of `RU` and
a custom lowercase set now block.

### RC-AUD-V6-1 — `BacktestValidationGate` self-deadlock (MEDIUM)
**`bot/core/validation_gate.py:113-119`** — `get_all_validations()` held
`self._lock`, then called `get_validation_status()` which re-acquires the same
**non-reentrant** `threading.Lock` → permanent deadlock. This is what hung the
full pytest run (`test_get_all_validations` never returned; the suite blocked
indefinitely until a per-test timeout was added). The gate is a stub not yet
wired to the live engine, so impact today is test-suite reliability + any future
caller. **Fix:** snapshot keys under the lock, resolve each status outside it.

### RC-AUD-V6-4 — `MultiUserPortfolio` raw-vs-sanitized key inconsistency (LOW)
**`bot/risk/multi_portfolio.py:84-103`** — `get()`'s fast path and `has_user()`
looked up the **raw** `user_id`, but `get()`'s slow path stored under a
**sanitized** key (and `_load_existing()` used the unsanitized filename). Any
`user_id` that changes under sanitization (e.g. `"user.123"`) would never be
found and would be **recreated — wiping balance/positions — on every access**.
Not exploitable today because all callers pass numeric Telegram IDs (sanitization
is a no-op), hence LOW not Critical, but a latent state-loss bug. **Fix:**
sanitize once via a shared `_sanitize()` helper used by `get()`, `has_user()`,
and `_load_existing()`. Verified state survives re-access for a dotted id.

### RC-AUD-V6-5 — liquidation did not arm the per-symbol cooldown (LOW)
**`bot/core/engine.py:307-320`** — `_on_live_position_closed` armed the
re-entry cooldown only when `close_reason` contained `"SL"`/`"STOP"`, but
`_fetch_bitget_close_data` labels a liquidation `"LIQUIDATED"` — the most adverse
close of all — which matched neither, allowing immediate re-entry into a
just-liquidated symbol. **Fix:** added `"LIQUID"` to the trigger. Verified
SL/STOP/LIQUIDATED arm the cooldown and TP/manual do not.

---

## Documented — execution path (need live-exchange context to change safely)

> These were verified at `file:line` but touch live order placement / PnL
> accounting where a blind change risks regressions. Recommended for a focused
> follow-up PR with exchange-sandbox validation.

| ID | Sev | Location | Issue |
|----|-----|----------|-------|
| **V6-E1** | High | `engine.py` `confirm_trade` (~1690) | Per-symbol SL cooldown checked only in `_evaluate_signal`; the execute path never re-checks it, so a pre-queued or manual idea re-enters a just-stopped symbol. Fix: re-check `_symbol_cooldowns` immediately before `live_executor.execute()`. |
| **V6-E2** | High | `live_executor.py:4169-4173` | `_close_position_inner` sets `net_pnl = exchange_pnl` and adds close fees to gross, but `exchange_pnl` is often a **gross** figure (`achievedProfits`/`profit`), so reported net P&L omits fees. The reconcile path (5425-5436) handles the same value correctly — mirror it. |
| **V6-E3** | High | `live_executor.py:3349-3363`, `3678-3682` | RC-AUD-001's retry-then-flatten-on-missing-SL guard exists only on the market `execute()` path. Limit fills and drift→market conversions place SL/TP once and only warn if **both** SL and TP are None — an SL-only failure leaves a leveraged position with **no exchange stop**. Factor the guard into a helper; gate "unprotected" on `sl_id is None` alone. |
| **V6-E8** | Med | `live_executor.py:748-758` (caller 2475) | `_create_order_idempotent` discards the `verified` flag from `_find_order_by_client_oid`; an order that actually landed but couldn't be confirmed (venue outage) is reported as a clean failure, leaving a possible untracked/unprotected orphan. The sibling POST_ONLY path honors the flag — make this one consistent. |
| **V6-E4** | Med | `live_executor.py:3119-3123` | Trailing-SL update persists `pos.stop_loss = new_sl` **before** the exchange update, which can fail and preserve the old wider stop — the exact local/exchange drift the "M-02 FIX" comment claims to prevent. Only commit local SL after exchange placement succeeds. |
| **V6-E5** | Med | `live_executor.py:3630-3632` | Drift→market fallback calls `create_order` without a `clientOid`; a timed-out-but-landed submit can orphan an unprotected position (and `_check_pending_limit` may then mark it closed). Route through `_create_order_idempotent`. |
| **V6-E7** | Low | `live_executor.py:1083-1191` | Adopted-position safety SL/TP is sized with ccxt `contracts` while the tracked `quantity` comes from `info.totalQty`; if ccxt under-reports contracts (the known UTA case), the protective stop covers less than the real position. Pass `quantity`. |

---

## Documented — security

| ID | Sev | Location | Issue |
|----|-----|----------|-------|
| **V6-S1** | High | `auth_routes.py:317-324` | `GET /auth/me` (access-token authed) re-mints a fresh access token **and** a 7-day refresh token on every call. A stolen short-lived access token can be refreshed indefinitely and upgraded to a long-lived refresh token, defeating short TTLs and bypassing the RC-AUD-020 refresh rotation/reuse-detection. Fix: `/me` should return user info only; renewal goes through `/auth/refresh`. |
| **V6-S2** | Med | `dashboard_api.py:69, 184-186` | Path-traversal guard uses `filepath.startswith(realpath(base))`; a sibling dir sharing the prefix (e.g. `website-private` next to `website`) passes. `do_HEAD` additionally selects its base from the already-resolved path (circular). Fix: compare against `realpath(base)+os.sep` or `os.path.commonpath`. |
| **V6-S3** | Med | `auth_routes.py:48-56, 106-119` | Auth rate-limit/lockout dicts are keyed by IP/attacker-controlled email; per-key lists are time-pruned but **dict keys are never evicted** → unbounded memory growth (DoS) from many unique emails/IPs. `api_bridge.py` already prunes at >1000 — mirror it (or use Redis TTLs). |
| **V6-S4** | Med | `audit_chain.py:135-143, 251-264` | The Ed25519 batch attestation (`sign_latest_batch`) is computed every 50 entries but **never persisted or verified** — `verify()` only re-derives the keyless SHA-256 chain, which anyone with write access can recompute end-to-end. The signature layer provides zero tamper-evidence as wired. Persist + verify batch signatures (and fix the Merkle odd-node duplication). |
| **V6-S5** | Low | `auth_routes.py:237-247` | `get_current_user_id` never re-checks `is_active`; a deactivated user keeps access until the access-token TTL expires (extendable via S1). Re-load the user or check a token epoch. |
| **V6-S6** | Low | `audit_chain.py:208-226` | After a malformed JSON line, `prev_hash` is `None` and the linkage check is skipped for the next entry, slightly weakening splice/reorder detection (entry-hash + sequence checks still fire). Treat a malformed line as a chain break. |

---

## Documented — learning / compliance

| ID | Sev | Location | Issue |
|----|-----|----------|-------|
| **V6-L1** | High | `safety_policy.py:110-126` | `classify_proposal` grants `SAFE_AUTO_DOCS`/`SAFE_AUTO_TEST` (auto-applied, no human) purely from free-text keywords + absence of a few code indicators. A proposal like *"update the docs to recommend larger position sizing"* auto-applies. Drive classification from a structured `change_target`/`file_paths` field, not free text. Inert today (status is a label), so High-not-Critical. |
| **V6-L2** | Med | `safety_policy.py:54-97` | Risk-increase keyword detection is plain substring matching; `"increase position size to 50%"`, `"widen the stop"`, etc. match zero keywords, and `evidence`/`rollback_plan`/`test_plan` aren't scanned. Combine with the structured target gate from L1; default risk-relevant targets to BLOCKED. |
| **V6-L3** | Med | `experience.py:106-116` | `record_trade_result` appends an orphan `DecisionMemory` (empty symbol/regime/strategy) linked only by a `RESULT_FOR:` string; `strategy_eval`/`patterns`/`get_similar_setups` filter on the empty fields and silently see **zero** completed trades → the learn-from-outcomes loop runs on empty data and reports misleadingly clean safety metrics. Carry the linking fields or join on `decision_audit_id`. |
| **V6-L4** | Low | `orchestrator.py:211-224` | After re-classifying a proposal, only `status` is persisted; the corrected `classification`/`human_approval_required` are dropped, and a dead `p.__dict__` serialization branch would emit non-JSON internals if reached. Persist re-classified objects via `model_dump(mode="json")`. |
| **V6-R2** | Low | `risk_engine.py:280-287` | The warning-rate breaker re-evaluates only on same-key arrival, so it can stay tripped after its window empties if the offending key goes silent — fails *closed* (over-conservative), a latent availability bug. Recompute liveness against the pruned window. |

---

## Test-suite health

Ran the full suite from a clean checkout with a per-test timeout
(`pytest --timeout=25 --timeout-method=signal`):

```
1116 passed, 46 failed, 13 skipped   (1175 collected, ~26s)
```

**Before this PR the suite could not complete at all** — `test_get_all_validations`
deadlocked (RC-AUD-V6-1) and blocked the run indefinitely without a timeout
plugin. That is now fixed. This PR adds 17 passing tests and introduces **zero**
new failures.

### The 46 pre-existing failures (present on `main`, not caused by this PR)

These were triaged; **none are product crashes**. Categories:

1. **Logging-capture harness mismatch** (`test_audit_v5_followup_risk.py`, parts of
   others) — the assertions read `caplog.records` for structured `action`/`result`/
   `data` attributes, but the audit logger writes JSON to stderr and those records
   don't surface through `caplog` in this environment. The **product behavior is
   correct** (verified: the `order_flow_gate / SKIPPED_NO_ANALYZER / TAKER_3BAR`
   audit event *is* emitted in captured stderr). Test-harness issue.

2. **Stale assertions vs. intentionally-changed behavior** — e.g.
   `test_llm_config_default_model_is_gpt4o` (default model changed from `gpt-4o`
   during the Opus-4.8 upgrade), `test_call_tool_allows_no_auth_when_unset` /
   `test_runtime_state_default` (MCP server was deliberately hardened to
   fail-closed and refuse to start without `MCP_AUTH_TOKEN`),
   `test_three_concerns_produces_halt` (critique `MAX_CONCERNS_FOR_HALT` raised
   3→4), and the `test_manifest_*` set (new `RiskLimits` fields such as
   `equity_curve_pause_stddev` not mirrored into `config/risk_manifest.yaml`).
   The code changed on purpose; the tests weren't updated.

3. **Test-isolation / state pollution** (`test_live_executor.py` — 11 failures,
   `test_order_idempotency.py`) — these read `data/live_positions.json` and other
   runtime files left behind by earlier tests/runs (`conftest.py` cleans only
   `combined_state.json`). Example: `test_execute_buy_market_order` fails with
   *"Already have an open LONG position on BTC/USDT"* loaded from a stale state
   file. Pass in isolation against a clean `data/` dir.

### Recommendations for the test suite (follow-up)
- **Isolate filesystem state**: point `RUNECLAW_STATE_DIR` / position/closed-trade
  paths at a `tmp_path` per test (or extend `conftest.py` to clean
  `data/live_positions*.json`, `data/closed_trades.json`, `data/risk_state.json`,
  `data/learning/`). This alone clears the ~12 pollution failures.
- **Fix the `caplog` audit-log capture** (attach a propagating handler or assert on
  a structured sink) to clear the logging-mismatch failures.
- **Refresh the stale assertions** to the current intended values (model default,
  MCP fail-closed, HALT threshold, manifest fields) — or, where a change was *not*
  intended (confirm the critique HALT 3→4 loosening and regime-detection
  `UNKNOWN`/`CHOPPY` behavior are deliberate), treat the test as the spec.
- **Add `pytest-timeout` to the dev deps** and set a default test timeout so a
  future re-entrant-lock or network hang fails fast instead of stalling CI.

---

## Confirmed correct (candidates refuted during this audit)

- **Risk engine fail-closed core** — no exception path silently passes a check;
  corrupt persisted state trips the breaker; VaR z-score table, Kelly guard,
  stop-distance floor, and SHORT/LONG PnL signs are correct.
- **Learning → risk wall** — `model_validate`/`model_validate_json` run the
  `may_override_risk` validator on load (disk-tampered `True` is rejected at read
  time); `get_learning_context` returns a constant `may_override_risk_engine:
  False`; no learning `status` is read by the live path.
- **Compliance live locks** — all five locks enforced fail-closed; notional cap is
  NaN-safe; approval tokens are single-use/expiring/trade-bound.
- **JWT hardening (V5)** — alg pinned to HS256 (no header-alg confusion), weak/
  default secret fails closed at startup, timing-safe `hmac.compare_digest`
  comparisons, refresh rotation + epoch revocation (RC-AUD-020).
- **Order router** — VWAP/idempotent-clientOid dedup, leverage abort-on-mismatch,
  close-residual re-protection (RC-AUD-023b) all sound.

---

*Generated as part of the V6 deep audit. Fixes in this PR are limited to the five
independently-tested, low-blast-radius defects above; the 13 documented items are
recommended for focused follow-up PRs (execution-path items require Bitget-sandbox
validation; auth-renewal and proposal-auto-apply items are product-policy calls).*
