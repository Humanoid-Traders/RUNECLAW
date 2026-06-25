# RUNECLAW Deep Audit Report V5

**Date:** 2026-06-25
**Scope:** The trading bot and all related functions — `bot/` (engine, risk, executor, analyzer,
telegram, compliance, learning, mcp, nlp, db), `api_bridge.py`, `bot/web/dashboard_server.py`,
`bot/api/auth_routes.py`, plus a secondary pass over the Node.js web layer (`app/`).
**Branch audited:** `claude/affectionate-rubin-43ta6o`
**Bot status:** LIVE on Bitget USDT-M Futures (isolated margin, micro-test mode) — real capital.
**Predecessor:** `docs/AUDIT_REPORT_V4.md` (2026-06-20, 62 found / 62 fixed). This V5 re-verifies the
prior guarantees still hold and finds what V4 missed.

---

## Summary

| Severity | Found | Fixed (this PR) | Documented / Remaining |
|----------|-------|-----------------|------------------------|
| CRITICAL | 3 | 3 | 0 |
| HIGH | 7 | 6 | 1 |
| MEDIUM | 11 | 4 | 7 |
| LOW | 6 | 1 | 5 |
| **Total** | **27** | **14** | **13** |

Every finding was **re-verified at exact file:line** by independent passes before inclusion;
findings carry a **Confidence** (separate from severity). Several candidates were **refuted** and are
listed under "Confirmed correct" so the report does not over-claim.

### Fix before the next live trade (the shortlist)
1. **RC-AUD-001** — a live position can be left with **no stop-loss** (classic/non-UTA path) and the
   bot reports success. *(Fixed)*
2. **RC-AUD-002** — **auto-confirm** executes live trades with **no human** in the loop and is **on
   by default** (threshold 0.75); it auto-mints the compliance "human-approved" lock. *(Fixed)*
3. **RC-AUD-015** — a **hardcoded `BOT_SYNC_SECRET`** is committed in `app/server.js`, allowing
   unauthenticated overwrite of trade/equity data via the sync endpoint. *(Fixed — operator must
   also rotate the leaked secret.)*

---

## Safety-Guarantee Assessment

The product thesis is *"the bot suggests, the human decides, the risk engine enforces."*

| Guarantee | Verdict | Evidence |
|-----------|---------|----------|
| **The bot suggests** | Holds | LLM/analyzer output is advisory; MCP excludes execution; the learning subsystem has a real hard wall (see below). |
| **The human decides** | **Broken by default** | Auto-confirm (`engine.py:720-752`) executes any idea with `confidence ≥ 0.75` via `confirm_trade(user_id="auto")`, and the compliance "HUMAN_APPROVED" lock is auto-minted in live mode (`engine.py:1414-1418`). With env-armed live mode (`is_live()` true), real orders fire with zero human action. **RC-AUD-002 / 018.** *(Fixed: auto-confirm now disabled by default and cannot place live orders without an explicit separate opt-in.)* |
| **The risk engine enforces** | Holds, with gaps | The 23-check fail-closed engine is well-built (corrupt-state → breaker tripped; atomic fsync; circuit-breaker persistence). Gaps: the manual-trade path skips several portfolio-safety checks (**RC-AUD-008**), and order-flow gates / size-reductions fail-open (**RC-AUD-011**). Position sizing, VaR z-scores, Kelly guards, and SHORT/LONG PnL signs are correct. |

**Learning cannot override risk — CONFIRMED enforced (not just asserted).** `RiskLimits`/`AppConfig`
are `@dataclass(frozen=True)`; there is **no** write path from `bot/learning/` to `CONFIG`, `RUNTIME`,
or risk state (grep for `setattr`/`object.__setattr__`/risk assignments → zero matches);
`PatternRecord.may_override_risk` raises on any non-`False` value (`models.py:189-197`);
"auto-applied" doc/test proposals only flip a status string and never edit code. The guarantee in
`tests/test_learning_cannot_override_risk.py` is met by construction.

---

## V4 Regression Check (spot-verification of prior fixes)

| Prior fix | Status | Evidence |
|-----------|--------|----------|
| C-05 (don't pop pending idea before success) | Holds | `confirm_trade` reads via `.get` (engine.py:1199); pops only on reject/after execute. |
| C-07 (SHORT limit SL/TP direction) | Holds | Limit recalc uses additive offsets per direction (engine.py:1292-1304). |
| C2-28 (VaR z-score lookup table) | Holds | `_VAR_Z_SCORES` table (risk_engine.py:1235-1241). |
| F-01 (circuit-breaker persistence) | Holds | `_load_state`/`_save_state`, corrupt → fail-closed (risk_engine.py:1312-1346). |
| C2-24 (stop-distance floor) | Holds | `stop_distance_pct = max(..., 0.001)` (risk_engine.py:372). |
| C-09 (checks #14/#15 cascade) | Holds | `margin_equiv_position_usd` initialized before try (risk_engine.py:679). |
| H-05 (fsync on position save) | Holds | atomic write + fsync in `_save_positions`. |

No regressions found in the sampled set.

---

## CRITICAL Findings

### RC-AUD-001: Live position kept open with NO stop-loss when the SL leg fails (classic path)
- **Severity:** Critical · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/core/live_executor.py:2106-2174` (caller), `:2250-2399` (`_place_sl_tp`), and the
  post-order emergency path `:2225-2242`; same pattern at adoption `:1113-1129`.
- **Issue:** On a non-UTA account, SL and TP are placed as two independent orders. An SL
  `create_order` exception is swallowed (logged `SKIP`) so `_place_sl_tp` returns `sl_id=None`. The
  caller sets `position.sl_order_id = None`, but the "UNPROTECTED" audit and the user-facing warning
  both gate on `sl_id is None AND tp_id is None` (`:2125`, `:2155`). So when **SL fails but TP
  succeeds**, there is **no warning at all**, the success card prints `" | SL: pending"` (`:2132`),
  and a normal green "Risk: ✅ APPROVED" message is returned — for a live, leveraged position with no
  stop. The UTA/v3 path (`_place_sl_tp_v3`) is atomic (`tpsl` strategy order) and is **not** affected.
- **Impact:** A transient venue error on the SL leg (trigger-too-close, 5xx) leaves an unbounded-loss
  live position with no protective stop and no operator alert. This is the single most expensive
  failure mode and directly violates "the risk engine enforces."
- **Fix:** Treat `sl_id is None` (the SL leg alone) as the unprotected condition; retry SL once, and
  if it still fails on a live position, **flatten the position** (market `reduceOnly`) and return an
  `EXECUTION FAILED — closed for safety` result. Never report success with a missing stop. Same
  warning-gating fix applied to the limit-fill and adoption paths. Regression test:
  `tests/test_sl_failure_protection.py`.

### RC-AUD-002: Auto-confirmation bypasses the human gate and is ON by default
- **Severity:** Critical · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/core/engine.py:720-752` (scan-tick) and `:1646-1666` (batch); default
  `bot/config.py:686` (`AUTO_CONFIRM_THRESHOLD = 0.75`); adaptive lowering `engine.py:690-718`;
  compliance auto-mint `engine.py:1414-1418`.
- **Issue:** Every scan tick auto-executes any pending idea with `confidence ≥
  RUNTIME.auto_confirm_threshold` via `confirm_trade(tid, user_id="auto")` — the comment says
  "bypass human confirmation gate and auto-execute." The default threshold is **0.75** (active out of
  the box), and adaptive logic can lower it to `adaptive_threshold_min` on a winning streak. The
  compliance engine's Lock 5 ("HUMAN_APPROVED") is satisfied by an approval token the engine mints
  for itself moments earlier (`:1414-1418`), so in live mode **all five locks pass with no human**.
- **Impact:** Directly contradicts the headline "human decides" guarantee. In env-armed live mode,
  real positions open autonomously off a partly-LLM-derived confidence score.
- **Fix:** Default `AUTO_CONFIRM_THRESHOLD` to **1.0** (disabled). Auto-confirm may place **paper**
  orders when explicitly lowered, but is **blocked from placing live orders** unless a separate
  `AUTO_CONFIRM_LIVE_ENABLED=true` opt-in is set; the block is logged/audited. Risk re-checks still
  run. Regression test: `tests/test_auto_confirm_gate.py`.

### RC-AUD-015: Hardcoded `BOT_SYNC_SECRET` committed in source → sync-endpoint auth bypass
- **Severity:** Critical · **Confidence:** High · **Status:** FIXED (this PR) — *operator must rotate
  the leaked secret*
- **File:** `app/server.js:14-18` (hardcoded 60-char fallback), consumed by `app/routes/sync.js:14-19`
  (`botAuth`, `:114-123`).
- **Issue:** When `BOT_SYNC_SECRET` is unset, the server assigns a **committed default secret**. Any
  reader of the repo can present it in the `X-Bot-Secret` header to call `POST /api/bot/sync` /
  `/api/bot/sync/scan`, which `DELETE FROM trades` and re-inserts (`sync.js:143-179`) — overwriting
  the operator's trade/equity/portfolio data and poisoning the public dashboard summary endpoints.
  Writes are scoped to `AUTHORIZED_BOT_USER_ID` (cannot target arbitrary users) but can clobber the
  operator and falsify displayed PnL.
- **Impact:** Auth bypass on a state-mutating endpoint; data-integrity / dashboard falsification.
- **Fix:** Remove the hardcoded fallback; **fail-closed** if `BOT_SYNC_SECRET` is unset or too short
  (refuse to start, matching the Node `JWT_SECRET` guard). The previously-committed secret value
  must be **rotated** by the operator since it is in git history.

---

## HIGH Findings

### RC-AUD-005: POST_ONLY retry regenerates the clientOid → double-submit / double-fill
- **Severity:** High · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/core/live_executor.py:1813-1840`.
- **Issue:** On a post-only-style rejection the code widens the price **and regenerates the
  clientOid** (`coid + "-r1"`, `:1836-1839`) before resubmitting. The branch fires on **any**
  exception whose stringified form contains a broad substring (`"post only"`, `"would immediately"`).
  If the original order actually landed but the client saw a timeout that matches, the resubmit uses
  a fresh idempotency key, so Bitget's clientOid dedup cannot catch it → two live orders.
- **Impact:** Duplicate position / double notional on the retry path.
- **Fix:** Before resubmitting under a new coid, reconcile the original via
  `_find_order_by_client_oid`; only resubmit if the original is **confirmed absent**. Pairs with the
  RC-AUD-006 fail-closed fix. Regression test: `tests/test_order_idempotency.py` (extended).

### RC-AUD-006: Idempotency reconciliation conflates "absent" with "lookup failed" (fail-open)
- **Severity:** High · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/core/live_executor.py:631-660` (`_find_order_by_client_oid`), `:662-710`
  (`_create_order_idempotent`).
- **Issue:** `_find_order_by_client_oid` swallows all fetch errors and returns `None` (`:658-660`).
  The caller treats `None` as "confirmed absent — safe to surface the failure" (`:709`), but `None`
  also means "I could not reach the venue." During an outage where the order landed but the lookup
  fails, the system fails open — the enabling precondition for RC-AUD-005's double-fill.
- **Impact:** Double-submit under exchange flakiness.
- **Fix:** Make the lookup tri-state — distinguish "verified absent" from "lookup failed" — and have
  callers fail-closed (do **not** treat unverifiable as absent; do not regenerate the key). Test as
  above.

### RC-AUD-008: Manual trades bypass portfolio-safety risk checks (loss-streak, cooldown, correlation)
- **Severity:** High · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/risk/risk_engine.py:340` (`is_manual`), skips at `:535-540, 586-624, 662-676`;
  manual ATR synthesized in `engine.py` so the volatility guard can't fail.
- **Issue:** When `idea.source == 'manual'`, the engine skips risk-reward, confidence,
  **correlation/concentration, loss-streak, and cooldown**. Skipping signal-opinion checks (R:R,
  confidence) is defensible — the user chose the levels — but skipping **loss-streak** and
  **cooldown** lets a user on tilt fire trade after trade past the very anti-revenge-trading guards,
  and skipping **correlation** lets manual entries stack one correlated group without limit. The
  manual path is also a live-execution path.
- **Impact:** A class of protective limits is disabled for exactly the emotionally-risky manual flow.
- **Fix:** Keep skipping only the signal-opinion checks (R:R, confidence). Re-enable the
  portfolio-safety checks that are not about signal quality — loss-streak, cooldown,
  correlation/concentration — for manual trades (max-positions, daily-loss, drawdown, margin-risk
  already run). Regression test: `tests/test_manual_trade_safety_checks.py`.

### RC-AUD-003: Unauthenticated read endpoints + permissive CORS default + `0.0.0.0` bind + raw error leakage
- **Severity:** High · **Confidence:** High · **Status:** PARTIALLY FIXED (this PR)
- **File:** `api_bridge.py:299-307` (CORS), `:334/347/619/645` (unauth `/health`,`/scan`,`/blackswan`,
  `/patterns`), `:737` (bind), `:369/477/557` (raw `{exc}` in responses).
- **Issue:** `/health`, `/scan`, `/patterns`, `/blackswan` have **no** auth dependency — any client
  drives `ccxt.fetch_ohlcv`/`fetch_order_book` and `/health` leaks `simulation_mode`,
  open-position count, and circuit-breaker state. CORS defaults to `*` (`:299`). Uvicorn binds
  `0.0.0.0` (`:737`). Raw exchange/library exception strings are returned to clients, including on
  the unauthenticated `/scan` (`:369`). *(The money endpoints `/confirm`, `/close`, `/halt`,
  `/portfolio` are correctly token-gated, and `allow_credentials` is correctly forced False under
  `*` — so this is High, not Critical.)*
- **Fix (this PR):** CORS default tightened to **same-origin** (empty) instead of `*`
  (`api_bridge.py`), and raw `{exc}` no longer interpolated into the `/analyze` and
  `/portfolio/open` client responses (generic message + server-side log). The `0.0.0.0` bind and
  read-endpoint auth are left as **documented recommendations** (binding localhost / adding a
  read-token needs operator/deploy coordination to avoid breaking the dashboard wiring).

### RC-AUD-016: Dashboard hardcodes `simulation_mode: True` → misreports live trading as paper
- **Severity:** High · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/web/dashboard_server.py:88`.
- **Issue:** `/api/state` returns a literal `"simulation_mode": True` instead of reading
  `CONFIG.simulation_mode`/`CONFIG.is_live()`. The dashboard shows "simulation" **even when trading
  live** — an operator glancing at it could believe real capital is safe when it is not.
- **Impact:** Misleading live-vs-sim indicator on a money-moving system.
- **Fix:** Report the real mode from `CONFIG`. Regression test in `tests/test_dashboard_state.py`.

### RC-AUD-017: Dashboard `/api/*` returns cross-user positions/equity/risk/cost
- **Severity:** High (when token unset) · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/web/dashboard_server.py:48-159`, bind `bot/main.py:107` (`0.0.0.0:8080`).
- **Issue:** `/api/state`, `/api/positions`, `/api/signals` dump every user's positions, equity,
  rejection history, LLM routing, and cost. Access is correctly fail-closed when `DASHBOARD_TOKEN` is
  set (`hmac.compare_digest`), but the port binds all interfaces, so the data is one missing-env-var
  away from public exposure.
- **Recommended fix:** Require the token unconditionally (no silent disable), bind localhost by
  default, and scope `/api/positions|signals` to the requesting operator.

### RC-AUD-018: Env-driven live mode auto-grants Lock 1 and auto-mints Lock 5 (no per-session human arming)
- **Severity:** High · **Confidence:** High · **Status:** OPEN (documented; mitigated by RC-AUD-002 fix)
- **File:** `bot/core/engine.py:85-91` (auto-grant `LIVE_TRADE`), `:1414-1418` (auto-mint approval
  token); `config.py:716-742` (`is_live()`).
- **Issue:** With `SIMULATION_MODE=false` + `LIVE_TRADING_ENABLED=true` + a chat allow-list,
  `LIVE_TRADE` is granted for the whole process lifetime and each trade's "human approval" token is
  minted by the engine — so the 5-lock gate's independent Lock 1 and Lock 5 provide no real per-trade
  human gating. Combined with auto-confirm this is the headline operational risk (now blocked by the
  RC-AUD-002 live opt-in).
- **Recommended fix:** Require an explicit runtime human arming step (e.g. `/golive CONFIRM`) even in
  env-driven live mode, and source the approval token from the actual Telegram button callback rather
  than minting it unconditionally.

---

## MEDIUM Findings

### RC-AUD-004: Callback IDOR — ownership check skipped when the owner tag is absent
- **Severity:** Medium · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/skills/telegram_handler.py:6109-6120` (confirm), `:6233-6244` (reject); helper
  `_uid_matches` `:1126-1138`.
- **Issue:** `expected_uid = parts[2] if len(parts) > 2 else None`; `_uid_matches(caller, None)`
  returns True (allow-all). A linked-but-unauthorized user who knows/guesses a pending `trade_id`
  could confirm/reject another user's idea via a crafted `confirm:<id>` callback. Bounded by needing
  a valid trade_id and a linked account; live-trade permission is separately gated.
- **Fix:** For state-changing callbacks, **deny** (fail-closed) when `expected_uid` is missing.
  Regression test: `tests/test_callback_idor.py`.

### RC-AUD-013: Raw ccxt/exchange exception strings echoed to users
- **Severity:** Medium · **Confidence:** Medium · **Status:** FIXED (this PR, API side)
- **File:** `api_bridge.py:477,557`; `bot/skills/telegram_handler.py:6140-6251`; executor returns at
  `live_executor.py:2186-2248`.
- **Issue:** Raw `{exc}` from the exchange/library is surfaced to the end user (HTTP `detail` and
  Telegram). ccxt error payloads can carry request context/account identifiers.
- **Fix (this PR):** API endpoints return a generic message and log the full exception server-side.
  Telegram/executor user-facing strings flagged for the same treatment (documented).

### RC-AUD-010: Limit-order confirm skips drift/past-SL re-checks, then reprices SL/TP without re-validation
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/core/engine.py:1221-1318`.
- **Issue:** For `order_type=='limit'`, the drift and R:R-deterioration guards are skipped, and the
  entry is recomputed to `current_price ± 0.5·ATR` with SL/TP rederived from original distances
  (`:1291-1304`) — but the recomputed SL is not re-validated against current price. Bounded by the
  executor's later past-SL guard (`live_executor.py:1528-1543`).
- **Recommended fix:** Re-run past-SL and stale-data checks on the **new** levels after recalc.

### RC-AUD-011: Order-flow gates (#22/#23) and macro/session size-reductions fail-open
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/risk/risk_engine.py:398-407` (macro `except: pass`), `:422-429` (session
  `except: pass`), `:811-825` (#22), `:827-839` (#23).
- **Issue:** Checks #22/#23 append to `passed` when no order-flow analyzer/signal is present
  (fail-open, contrary to the module's fail-closed contract). Macro/session size-reduction
  multipliers are wrapped in `except: pass`, so a provider hiccup silently leaves full-size positions
  (the notional cap still bounds exposure). `OrderFlowAnalyzer` is normally wired, lowering real-world
  exposure.
- **Recommended fix:** Make #22/#23 fail-closed or explicitly config-gated; emit an audit event when a
  size-reduction multiplier is dropped due to exception (fail toward the smaller size).

### RC-AUD-007: VaR returns magic tuples; skip-vs-reject sentinel is fragile
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/risk/risk_engine.py:1187-1261` (returns `(-1,-1)` skip / `(0.0,100.0)` zero-equity),
  caller `:794-809` (skip when `proposed_var < 0`).
- **Issue:** Dual-meaning return tuple. The behavior is correct today, but a future edit treating
  `current_var==0.0` as "no data" would silently disable VaR.
- **Recommended fix:** Replace the magic tuples with an explicit result type (`SKIP|OK|REJECT`); add a
  unit test pinning the zero-equity → reject behavior.

### RC-AUD-019: `load_dotenv(override=False)` lets inherited env silently flip the safety switches
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/config.py:18`.
- **Issue:** Pre-existing process/OS env wins over `.env`. An inherited `SIMULATION_MODE=false` or
  `LIVE_TRADING_ENABLED=true` silently overrides the operator's `.env`, pairing dangerously with the
  env-driven live arming (RC-AUD-018). The "empty safety switch ⇒ True" guard mitigates only the
  *empty* case, not the *inherited-and-set* case.
- **Recommended fix:** Document the precedence in deploy docs; consider making `SIMULATION_MODE=true`
  a hard veto, and warn at startup when a safety switch comes from the inherited environment.

### RC-AUD-020: No JWT revocation / refresh-token reuse detection
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/api/auth_routes.py:97-110` (verify), `:210-224` (refresh).
- **Issue:** Verification checks only signature + `exp`. Logout cannot invalidate a token; a leaked
  access token is valid 1h and a refresh token **7 days**, rollable forward with no reuse detection.
- **Recommended fix:** Add a token version/blacklist (Redis), a `/logout` that bumps it, and
  refresh-rotation reuse detection.

### RC-AUD-021: Position can be stranded in `"closing"` status on mid-close crash (unmonitored)
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/core/live_executor.py:3514-3515` (sets `"closing"` before await), monitor filter
  `:2641` (`status in ("open","pending_fill")`).
- **Issue:** A crash after setting `"closing"` but before the close completes persists the position as
  `"closing"`, which is excluded from SL/TP monitoring and from orphan re-adoption (a local record
  exists). No automated recovery.
- **Recommended fix:** On startup, revert stale `"closing"` records to `"open"` and re-protect, or add
  `"closing"` to the monitored/recoverable set with a timeout.

### RC-AUD-022: Orphan-adoption safety SL/TP failure is swallowed (adopted position left unprotected)
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (documented; same class as 001)
- **File:** `bot/core/live_executor.py:1113-1129`.
- **Issue:** When adopting an exchange position, the safety `_place_sl_tp` failure is caught and the
  position is adopted anyway without protection and without an UNPROTECTED alert.
- **Recommended fix:** Apply the RC-AUD-001 treatment — alert on SL failure and retry; surface
  unprotected adopted positions to the operator.

### RC-AUD-023: Partial-fill over-statement (entry) and residual orphan (close)
- **Severity:** Medium · **Confidence:** Medium · **Status:** OPEN (documented)
- **File:** `bot/core/live_executor.py:1893-1932` (entry full-qty fallback), `:3596-3683` (close
  fallback).
- **Issue:** If fill-quantity fetches fail, the bot books the full **requested** qty (`ESTIMATED`);
  SL/TP are then sized to an inflated quantity (clamped by `reduceOnly`, so over-statement not
  reverse-open). A partial market **close** can leave residual exchange exposure while the local
  record is marked fully closed — recoverable only on the next adoption sweep (which loses the
  original SL/TP levels).
- **Recommended fix:** Reconcile actual filled/closed quantity against the exchange before finalizing;
  re-open tracking for any residual.

### RC-AUD-025: Critique gate is fail-open and "user-confirmed proceed" applies to auto-confirmed trades
- **Severity:** Medium · **Confidence:** High · **Status:** OPEN (mitigated by RC-AUD-002 fix)
- **File:** `bot/core/engine.py:1364-1404`.
- **Issue:** The adversarial critique gate proceeds on error (fail-open), and a post-critique
  sub-min-confidence trade is allowed to proceed under "user already confirmed" — which also fires
  for `user_id="auto"`. With auto-confirm now blocked from live, the live blast radius is removed, but
  the fail-open-for-auto rationale remains in paper.
- **Recommended fix:** Distinguish human-confirmed from auto-confirmed before applying the
  "proceed anyway" rationale.

---

## LOW Findings

### RC-AUD-024: Security-sensitive dependencies float (`cryptography>=41`, `Pillow>=10.0`, `fastapi>=0.110`)
- **Severity:** Low · **Confidence:** High · **Status:** FIXED (this PR)
- **File:** `bot/requirements.txt`, `pyproject.toml`.
- **Issue:** `cryptography>=41.0.0` permits known-vulnerable 41.x/42.x; `Pillow>=10.0` permits
  versions with image-RCE CVEs; `fastapi>=0.110` pulls an unpinned starlette (past multipart DoS).
  For a process holding Bitget API secrets these should be pinned.
- **Fix:** Pin `cryptography`, `Pillow`, `fastapi`, `uvicorn` to current patched floors; keep
  `pip-audit` in CI.

### RC-AUD-012: In-memory per-IP rate limiter is bypassable and not multi-worker safe
- **Severity:** Low · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `api_bridge.py:99-122`.
- **Issue:** Keyed on `request.client.host`; behind a proxy every request shares one bucket (or
  becomes spoofable via `X-Forwarded-For`); state is per-process. Availability concern only.
- **Recommended fix:** Move rate limiting to the reverse proxy or a shared store; validate
  `X-Forwarded-For` against a trusted-proxy allowlist.

### RC-AUD-014: Prompt-injection sanitizer is a thin denylist applied only at the chat entrypoint
- **Severity:** Low · **Confidence:** High · **Status:** OPEN (documented; bounded by execution gate)
- **File:** `bot/skills/telegram_handler.py:103-135`, applied only at `:1095`.
- **Issue:** ~12-phrase regex denylist, trivially bypassable, and only on the free-form chat path.
  Bounded because the LLM chat path has no execution authority — trades still require
  `confirm_trade` → compliance → executor.
- **Recommended fix:** Treat as defense-in-depth only; rely on structural isolation and never let LLM
  output choose side/size/symbol without numeric re-validation.

### RC-AUD-026: Auth rate-limits are per-IP only (no per-account throttle)
- **Severity:** Low · **Confidence:** High · **Status:** OPEN (documented)
- **File:** `bot/api/auth_routes.py:44-66`; Node `app/auth.js:39-54`.
- **Issue:** Distributed/rotating-IP credential stuffing against one account is not throttled.
- **Recommended fix:** Add a per-account failure counter with backoff.

### RC-AUD-028: Node dev-mode auto-generates `JWT_SECRET`; unauthenticated market proxy; hardcoded-upstream `proxy.js`
- **Severity:** Low · **Confidence:** Med · **Status:** OPEN (documented)
- **File:** `app/server.js:6-13`, `app/routes/market.js`, `app/proxy.js`.
- **Issue:** Non-production runs use a per-process random JWT secret (tokens don't survive
  restart/replicas); `/api/market/*` is an unauthenticated outbound-fetch surface; `proxy.js`
  blind-proxies to a hardcoded third-party host with `'unsafe-inline'` CSP.
- **Recommended fix:** Require `JWT_SECRET` in all modes; rate-limit/authenticate the market proxy;
  confirm `proxy.js` is not deployed in production.

---

## Confirmed correct (refuted candidates — do not re-tread)
- **SHORT/LONG sign handling** is correct: realized/unrealized PnL (`portfolio.py:193-195,366-397,
  475-477`), close side (`live_executor.py:3519`), "price past SL" guards (`engine.py:1238-1247`).
- **`reduceOnly`/`tradeSide:close`** on the close path prevents a close from opening a reverse
  position (`live_executor.py:3567-3573`).
- **Bearer comparison** is timing-safe and fails-closed (503) when unset (`api_bridge.py:316-329`).
- **JWT verification pins HS256** by recomputing the MAC and ignoring the header `alg` → no
  alg-confusion / `alg:none` (`auth_routes.py:97-110`); `JWT_SECRET` hardcoded-default guard raises at
  startup (`:70-79`).
- **All Node SQL is parameterized**; trades routes are per-user scoped (no IDOR).
- **Fail-closed corrupt-state load** for the circuit breaker (`risk_engine.py:1335-1346`).
- **Stop-distance floor + notional-cap ordering** defuse the divide-by-near-zero sizing blowup
  (`risk_engine.py:372,449-460`); **VaR z-score table** (C2-28) and **Kelly edge guards** are sound.
- **Learning cannot override risk** — enforced by frozen config + absence of any write path
  (see Safety-Guarantee Assessment).
- **v3/UTA SL+TP is atomic** (`tpsl` strategy order) — RC-AUD-001 does not apply to UTA accounts.

## Appendix

**Files reviewed (primary):** `bot/core/engine.py`, `bot/core/live_executor.py`,
`bot/risk/risk_engine.py`, `bot/risk/portfolio.py`, `bot/config.py`, `api_bridge.py`,
`bot/web/dashboard_server.py`, `bot/api/auth_routes.py`, `bot/db/models.py`,
`bot/compliance/compliance_engine.py`, `bot/learning/{safety_policy,orchestrator,store,models}.py`,
`bot/skills/telegram_handler.py`, `bot/requirements.txt`, `pyproject.toml`.
**Files reviewed (secondary, Node):** `app/server.js`, `app/auth.js`, `app/db.js`, `app/proxy.js`,
`app/routes/{sync,trades,market}.js`.

**Checks NOT performed / next pass:** full sweep of `_place_sl_tp_v3` retry loop and the
trailing-stop update path; `bot/core/order_flow.py` math; a complete `app/` route inventory; live
chaos/fault-injection testing (exchange-down, WS-flap) against the SL-failure path; `pip-audit`
against a live advisory DB.

**Regression tests added (`tests/test_audit_v5_fixes.py`, all passing):**
`test_auto_confirm_disabled_by_default` (RC-AUD-002), `test_manual_trade_blocked_by_loss_streak` and
`test_manual_trade_blocked_by_cooldown` (RC-AUD-008), and `test_find_order_verified_absent` /
`_unverified_on_outage` / `_found_is_verified` (RC-AUD-006). RC-AUD-001's flatten path is verified by
code change + inspection; a full end-to-end test is deferred because the repo's existing
`execute()`-level exchange mock does not stub the market/leverage calls that the latest `main`
(`18e3ab4`, "leverage sync") added — the maintainers' own `execute()` tests currently fail in a bare
environment for the same reason. Updating that mock (stub `markets`/`load_markets`/`set_leverage`/
`fetch_balance`) is the recommended next step to cover RC-AUD-001 and the post-only double-fill guard
(RC-AUD-005) end-to-end.

**Note on the live code:** this audit was re-based onto current `origin/main`, which now ships a 24th
risk check (the "warning rate breaker", `18e3ab4`) on top of the 23 documented above. Line numbers in
this report are as of the merged HEAD; `live_executor.py` grew ~330 lines vs. the V4 baseline.
