# ⚔️ RUNECLAW — Repo, Bot, Web & UX Audit (Deliverable A)

**Auditor:** Lead Engineer / UX / QA · **Date:** 2026-07-15 · **Scope:** `github.com/Humanoid-Traders/RUNECLAW` only
**Method:** static analysis + tooling (`ruff` 0.15.8, `mypy` 1.19.1, `bandit` 1.9.4, `pip-audit` 2.10.1, `pytest` 9.0.2) + 3 parallel deep-review passes. Read-only; no code changed.
**Tools not available in this env (recommended for CI):** `deptry`, `gitleaks`, Lighthouse, Playwright, axe-core. Substitutes used are noted inline.

---

## 0. Executive summary

**No P0 (no crash/data-loss/committed-secret) issues.** The codebase is, on the whole, **above average**: clean PTB v20 async, tamper-evident redacted audit logs, a hard allowlist on privileged commands, zero committed secrets, an ultra-light landing page, and a disciplined custom CI flake-filter. The gaps are specific and fixable.

**Top risks to fix first (P1):**

| # | Tag | Finding | Evidence |
|---|-----|---------|----------|
| 1 | SEC | Read commands `/status` `/signals` `/playbook` `/risk` are **ungated** → on a live bot any stranger sees operator equity/PnL/positions | `telegram_handler.py:5313,7268,5657,5264` |
| 2 | BUG | **No global PTB error handler** anywhere → uncaught handler exceptions fail silently; no Sentry | `add_error_handler` absent repo-wide |
| 3 | BUG/UX | Web chat error **loses the user's typed message** and offers no retry | `app/public/js/chat.js:102-117` |
| 4 | A11Y | Chat drawer + trade modal: **no focus-trap / `aria-modal` / focus-return** | `dashboard.html:46`; `dashboard.js` |
| 5 | SEC/DX | CI `pip-audit` is **blind to fastapi/uvicorn/redis/PyJWT/Pillow + all transitive deps** (audits a 10-pkg lock). Real CVEs exist unseen: pyjwt 2.7.0, urllib3 2.6.3 | `ci.yml:59`, `requirements.lock` |
| 6 | DX | **aiohttp version drift**: `pyproject` pins 3.13.4, everything else 3.14.1 → `pip install .` ships an untested aiohttp | `pyproject.toml:19` |
| 7 | SEO | **`robots.txt` + `sitemap.xml` absent** repo-wide | — |
| 8 | DX | **`/version` missing** — no version constant exists anywhere | — |
| 9 | TEST | `smart_exits.py` (693 LOC exit logic) & `nlp/sanitize.py` (security) have **no dedicated test** | — |

Full severity-ranked list in §13.

---

## 1. Repo tree (2 levels) + branch map

```
RUNECLAW/
├── bot/            api backtest compliance core db formatters learning llm macro
│                   marketing mcp nlp prompts risk skills utils warroom web
├── app/            (Node.js) public/ routes/ lib/ data/ test/   ← product web app + chatbot
├── website/        marketing assets (jpg/webp heroes, demo-recording.webm, badges)
├── config/         risk_manifest.yaml etc.
├── dashboard_static/  docs/  playbooks/  scripts/  tests/  data/  evidence/
├── ollama/         agentbench/  demo/  logs/
├── pyproject.toml  requirements-ci.txt  requirements.lock  bot/requirements.txt
├── deploy.sh  watchdog.sh  api_bridge.py
└── .github/workflows/ci.yml   (single workflow)
```

**Branch map:** `main` (default, protected) + the active dev branch `claude/complete-audit-test-report-3hw8kt` + **~45 stale `claude/*` feature branches** (accounts-cap-display, backtest-*, calibration-*, chart-*, …). Recent `main`: #378 live-auth safe-halt → #377 per-user breakers → #376 web hardening → #375 livebalance routing → #374 venue safety.
🟡 **P3 (hygiene):** ~45 merged/abandoned `claude/*` branches should be pruned — they clutter the branch list and obscure active work.

---

## 2. Python + PTB version matrix; v20 idiom consistency

| Item | Value | Verdict |
|------|-------|---------|
| Python (runtime) | 3.11.15; `requires-python >=3.11` (`pyproject.toml:11`) | OK |
| CI Python | **3.11 only** — no matrix (`ci.yml:23`) | 🟠 P2 |
| python-telegram-bot | **`==22.7`** pinned in all 4 dep files (`pyproject.toml:16`) | OK |
| PTB idioms | `Application.builder()`, `ContextTypes.DEFAULT_TYPE`, `concurrent_updates(True)`, all handlers `async def` | ✅ clean v20 |
| v13 residue | **none** — no `Updater(`, `dispatcher`, `use_context`, `.idle()`, `run_polling` misuse | ✅ |

**Verdict:** PTB v20 async is applied consistently. No sync/v13 patterns to remove.

---

## 3. pyproject / requirements sanity

- **4 dependency files, hand-maintained and drifting:** `pyproject.toml`, `requirements-ci.txt`, `requirements.lock`, `bot/requirements.txt`.
- Runtime vs dev separation is clean *in pyproject* (runtime `dependencies`; extras `crypto/anthropic/charts/dev`, lines 26-47), but **CI ignores the extras** and installs the flat `requirements-ci.txt`, which re-pins everything → the drift source.
- 🔴 **P1 — aiohttp drift:** `pyproject.toml:19` = `aiohttp==3.13.4`; `requirements-ci.txt:12` / `requirements.lock:11` / `bot/requirements.txt:8` = `aiohttp==3.14.1`. `pip install .` yields the older, CI-untested lib.
- 🟠 **P2 — cryptography drift:** `pyproject.toml:28` floor `>=43.0.1` vs `>=48.0.1`/`==48.0.1` elsewhere.
- 🟡 **P3 — `requirements.lock` is mislabeled:** header says "generated from pip freeze" but lists only **10 direct** packages, no transitive deps → not a real lock.
- 🟡 **P3 — dev-tool pins float in CI:** `bandit>=1.8.0`, `pytest-cov>=6.0.0`, `pip-audit>=2.9.0` (`requirements-ci.txt`) vs exact pins in pyproject.

---

## 4. Dependency health (SCA)

**`pip-audit` against the full installed environment** flags CVEs that CI never sees (CI scopes to the 10-pkg lock — see §7):

| Package | Installed | Advisories | Fixed in |
|---------|-----------|-----------|----------|
| **pyjwt** | 2.7.0 | PYSEC-2025-183, 2026-120/175/177/179 (multiple) | 2.13.0 |
| urllib3 | 2.6.3 | PYSEC-2026-141/142 | 2.7.0 |
| setuptools | 68.1.2 | PYSEC-2025-49, 2026-1918/3447 | 78.1.1+ |
| pip | 24.0 | PYSEC-2026-196/1796/2875/2876 | 26.x |
| pytest | 8.3.5 | PYSEC-2026-1845 | 9.0.3 |
| wheel | 0.42.0 | CVE-2026-24049 | 0.46.2 |

🔴 **P1 (SEC):** **pyjwt 2.7.0 sits in the web-app auth path** (JWT sessions) with several open advisories — highest priority to bump. urllib3 next.
- `deptry` (unused/missing deps) not installed → substitute: `ruff F401` shows **87 unused-import hits** across `bot/` (dead-import proxy; not a true dep graph). Recommend adding `deptry` to CI.

---

## 5. Lint / type

| Check | Result |
|-------|--------|
| ruff (CI-gated: E9,F821,F811 on bot+tests; F401,F541 on bot) | ✅ green |
| ruff (**full default ruleset**, whole repo) | ~**900 issues**: 324×I001 (import sort), 193×E501 (line len), 87×F401, 63×F841 (unused var), 57×E402 — all **style/hygiene, non-gated** 🟡 P3 |
| ruff F401/F541 scope | **`bot/` only — `tests/` excluded** (asymmetric vs the E9 gate which covers both) 🟠 P2 `ci.yml:43` |
| mypy (CI-gated 13-file allow-list) | ✅ `Success: no issues found` |
| **mypy `--strict` on all `bot/`** | **~1,558 errors** — strict typing is aspirational; CI ratchets a narrow subset 🟡 P3 |

**Verdict:** the *gated* surface is clean and green; the repo is not strict-typed or fully lint-clean outside the gate. This is a deliberate ratchet, not a regression — but the F401 tests/ asymmetry (P2) is an easy tighten.

---

## 6. Secrets scan

`gitleaks` not installed → substitute: targeted `git grep` for token/secret assignment patterns + the Telegram token regex `[0-9]{6,}:[A-Za-z0-9_-]{30,}` across tracked `*.py/*.js/*.json/*.yml`.

- ✅ **0 hardcoded secrets** in tracked code.
- ✅ **No committed `.env`**; `.env` is gitignored.
- ✅ `.env.example` (465 lines, 71 `KEY=`) — every secret key **empty**; only safe config defaults populated (`SIMULATION_MODE=true`, `LIVE_TRADING_ENABLED=false`).
- ✅ `app/data/.jwt_secret` exists on disk but is **untracked + gitignored** (runtime artifact, not leaked).
- ✅ Token handling in code is safe: sourced via `_env_secret("TELEGRAM_BOT_TOKEN")` (quote/whitespace-stripped), passed only to `.token()`, redacted by the JSON logger.

**Verdict:** clean. Recommend adding `gitleaks` to CI as a standing guard.

---

## 7. CI review

**One workflow — `.github/workflows/ci.yml`** (job `test`, `ubuntu-latest`, 20-min timeout). Triggers `pull_request` + push to `main` + `workflow_dispatch`; concurrency `cancel-in-progress`.

| Aspect | State |
|--------|-------|
| Python matrix | ❌ 3.11 only 🟠 P2 |
| pip cache | ✅ `cache: pip` |
| Artifacts / release automation | ❌ none (`deploy.sh`/`watchdog.sh` unwired) 🟠 P2 |
| Node `app/` CI | ❌ **none** — `app/test/gateway_routes.test.js` never runs; no `npm test`/`npm audit` 🟠 P2 |
| ruff / mypy / bandit | ✅ gated (scopes in §5) |
| **pip-audit scope** | 🔴 **P1** — `-r requirements.lock` (10 pkgs) → fastapi, uvicorn, redis, **PyJWT**, Pillow + all transitive **unaudited** |
| test gate | ✅ `scripts/ci_test_gate.py` — full suite, per-failure isolated re-run (flake filter), baseline diff vs `known_failures.txt` (empty=strict), 60% coverage floor on `bot/risk`, `live_executor.py`, `bot/compliance` |

**Verdict:** the *design* of the gate (flake-filter + coverage floor + hash-chain audit) is strong; the SCA blind spot (P1) and the missing Node CI (P2) are the real holes.

---

## 8. Tests: coverage + gaps

- **331 test files, ~3,545 test functions, 148 `bot/` modules.** Direct coverage ≈ **132/148 (89%)** by module reference.
- pytest-asyncio `mode=auto`; `conftest.py` autouse fixture wipes `data/*.json` between tests (cross-test contamination guard). No markers registered.
- `bot/risk/` **6/6 tested** (good). `bot/web/` 2/2. `bot/skills/` 10/11. `bot/core/` 56/68.

**Untested, money/security-critical (P1):**
- `bot/core/smart_exits.py` — **693 LOC** exit logic, imported by 3; only indirect engine test.
- `bot/nlp/sanitize.py` — 63 LOC **input sanitization (security)**, imported by 3; no direct test.

**Untested (P2):** `limit_entry.py` (381), `exchange_sync.py` (458, reconciliation); orphaned `book_analysis.py`, `market_cap.py`, `seasonality.py`, `marketing/channel_forwarder.py`.

---

## 9. Bot review — `bot/skills/telegram_handler.py` (411 KB) + `bot/main.py`

**Strengths:** `concurrent_updates(True)` with documented money-path locking (`:269`); safe token handling; **tamper-evident redacted JSON audit logs** with SHA-256 hash chain (`utils/logger.py`); catch-all `MessageHandler(filters.TEXT & ~filters.COMMAND)` registered **last** (no shadowing); prompt-injection sanitization; single redacting `_send()` chokepoint (splits >4000 chars, HTML→plain fallback); **per-user `RateLimiter`** (20/min sliding window, memory-pruned); **hard allowlist + role gate** on privileged/destructive commands & callbacks; populated role-aware "/" command menu on startup.

**Findings:**
- 🔴 **P1 (SEC) — ungated read commands:** `_cmd_status` (`:5313`), `_cmd_risk` (`:5264`), `_cmd_signals` (`:7268`), `_cmd_playbook` (`:5657`) carry **no `@guard` and no allowlist/rate-limit check**. `/status` reads `engine.live_executor` (`:5326`) → leaks operator **live equity, open-position count, daily PnL, venue, loss-streak** to any caller on a live bot. (`_cmd_analyze` at `:4840` is correctly `@guard`ed — these slipped the net.)
- 🔴 **P1 (BUG) — no global error handler:** `add_error_handler` is absent repo-wide → uncaught exceptions in directly-registered handlers get PTB's default (log-only) → **silent failure, no user reply, no central capture**. No Sentry.
- 🔴 **P1/🟠P2 — `/version` missing** (no version constant exists anywhere; not in `/status`); **`/signal` singular missing** (only plural `/signals`).
- 🟠 **P2 — no SIGTERM handler:** clean teardown runs only on SIGINT (`main.py:343`); `docker stop`/systemd SIGTERM skips the `finally` block (`engine.stop()`, session teardown). Hand-rolled event loop bypasses PTB's built-in signal handling.
- 🟠 **P2 — polling-only:** `app.updater.start_polling` (`main.py:300`); no `run_webhook`/`set_webhook` path for prod.
- 🟠 **P2 — no `update_id` log correlation** despite `concurrent_updates` → interleaved task logs can't be stitched.
- 🟠 **P2 — `/start` latency:** serial awaited `fetch_open_orders` + `fetch_positions` before replying, **no typing indicator** → multi-second hang in live mode.
- 🟠 **P2 — no `ChatAction`/typing indicator anywhere** during LLM calls/scans.
- 🟠 **P2 — `/status` lacks uptime / last-scan / LLM-latency / version**; `/help` is a single wall-of-text (no inline menu / progressive disclosure).
- 🟡 **P3:** all handlers in group 0; a plain `logging.getLogger` coexists with the JSON logger (mixed structure); `threading.Lock` in the async rate limiter; no "More →" pagination.

98 command handlers total. Required set: **`/start` `/help` `/status` `/risk` `/playbook` present**; **`/version` `/signal` missing**.

---

## 10. Website review

⚠️ **Lighthouse / axe / Playwright were not run** (no live server + tools absent). Measured proxies below; recommend adding **Lighthouse CI + axe-core** to CI as a standing budget gate.

- **Perf (measured proxies):** landing first-party JS ≈ **13.5 KB uncompressed / ~4.5 KB gzip** (`icons.js` 7 KB + one inline block) — **well under the 180 KB budget**. **Zero `<img>`** (all inline SVG). One blocking stylesheet (24 KB/5.7 KB gz). Font: single Cinzel-700 woff2 **preloaded** with `font-display:swap`; body uses system stack (zero webfont cost). External scripts (Google/Telegram identity) injected `async` **only when configured**. **Strong.**
- **Responsive:** mobile-first (unprefixed base + `min-width` enhancements); `viewport-fit=cover`; tables in `overflow-x:auto`; hero `clamp()`. Handles 360px. **OK.**
- **Broken links:** none dead in code; docs → GitBook, Telegram → `t.me/HTRUNECLAW_bot`.
- 🟠 **P2 — tap targets < 44px:** `.btn` ≈ 38px (`styles.css:205`), `.btn--sm`/`.chat-close` ≈ 26px, `.nav-links a` ≈ 34px. (Most pass the 24px AA floor; fail the 44px AAA target.)
- 🔴 **P1 (SEO) — `robots.txt` + `sitemap.xml` absent** repo-wide.
- 🟠 **P2 (SEO):** missing `og:type`, `og:url`, `og:image:alt`, Twitter Card, `rel=canonical`. Favicon set + `og:title/description/image` + manifest + theme-color **present**.

---

## 11. Web chatbot review — `app/public/js/chat.js` + `app/routes/chat.js`

(Ships on the **dashboard**, not the landing page.)

**Present:** `fetch` POST `/api/chat` → bot gateway; **server-side persistence across reload** (`/api/chat/history`); ARIA live region on transcript; ESC-to-close; focus-to-input on open; pending-trade Confirm/Cancel cards with a **LIVE/PAPER risk badge** (guardrail); 429/503/network error branches; server-side rate limit (15/min).

**Gaps:**
- 🔴 **P1 (BUG) — error loses the message + no retry:** `input.value=''` runs *before* the request (`chat.js:102`), so on 5xx the typed text is gone and there's **no Retry**.
- 🔴 **P1 (A11Y) — no focus trap / not `aria-modal` / background not `inert`:** on mobile the drawer is a full-screen `inset:0` takeover yet SR/Tab reach content behind it; focus isn't returned to the FAB on close. Same for `tradeModal`. (WCAG 2.4.3 / 4.1.2)
- 🟠 **P2 — no streaming; static "Thinking…" bubble** (no animated dots/shimmer, no token stream) → perceived-latency hit on ~45 s LLM calls.
- 🟠 **P2 — no example-prompt chips (first open), no follow-up chips, no per-message copy/thumbs/regenerate.**
- 🟠 **P2 — minimal markdown:** whitelist `b/i/code/pre/br` only; no lists/headings/links/tables, no syntax highlighting.
- 🟡 **P3 — no voice input; chat input row lacks safe-area padding.**

---

## 12. UX AUDIT (dedicated)

**First-run:** Landing is fast and lean; copy is genuinely on-brand. Telegram `/start` has a rich inline keyboard + i18n welcome — but **blocks on serial exchange calls with no typing cue** (P2). Web chat first-open is a **plain greeting with no starter chips** (P2).

**Empty / loading / error states:** **Best-in-class on web** — one canonical skeleton→data→empty→error state machine (`app.js:155-183`) with **sharp, action-oriented empty copy** ("No closed trades yet — your history and journal live here.") and a Retry on error. The **chat** is the exception: no streaming feedback and it **destroys the user's message on error** (P1).

**Copy / brand voice:** ✅ Sharp, tactical, calm-confident, zero fluff — "refuses anything its risk engine doesn't like", "never invented numbers". Consistent across landing + chat greeting + Telegram. **Strength.**

**Mobile ergonomics:** viewport + safe-area on the tab bar are handled; **tap targets run small (26–38px)** and the **chat input row misses safe-area** (P2/P3).

**Motion + feedback:** ✅ Tasteful 150–180 ms transitions; **comprehensive `prefers-reduced-motion`** (zeroes all durations). **Strength.**

**Accessibility (WCAG 2.2):**
- ✅ Global `:focus-visible` gold ring (2.4.7/2.4.11); skip link (2.4.1); ARIA live regions on toasts/forms/chat (4.1.3); labeled nav/dialog; decorative SVG `aria-hidden`; clean heading order.
- **Contrast (computed):** body `#edeef2` on `#0a0b10` = **16.96:1** ✅; gold CTA label = 8.89:1 ✅; **but "LIVE — REAL MONEY" badge (white on red `#e5484d`) = 3.91:1 — FAILS 1.4.3** (P2, `styles.css:331`).
- 🔴 **P1 — chat drawer/modal lack focus-trap + `aria-modal`** (2.4.3/4.1.2).
- 🟡 **P3 — ARIA tab pattern incomplete** (no roving arrow keys / `role=tabpanel`).

> ⚠️ **Brand-palette discrepancy (decision required before E):** the brief specifies `bg #0B0D10 / text #C6CBD1 silver / CTA #FF6A2C ember`. The **shipped tokens** (`styles.css:16-59`) are `--bg #0a0b10`, text `#edeef2`, **CTA gold `#cbb06a`** — there is **no orange in the codebase**. The shipped gold system passes AA everywhere; the briefed orange-CTA-with-white-label would **fail at 2.86:1**. Recommend **keeping gold** unless a deliberate rebrand is intended.

---

## 13. Consolidated bug + UX-debt list (severity-ranked)

### 🔴 P1 — fix first
| Tag | Item | File:line |
|-----|------|-----------|
| SEC | `/status` `/signals` `/playbook` `/risk` ungated → operator financials leak on live bot | `telegram_handler.py:5313,7268,5657,5264` |
| BUG | No global PTB error handler → silent failures; no Sentry | repo-wide |
| BUG/UX | Web chat error destroys user's message + no retry | `app/public/js/chat.js:102-117` |
| A11Y | Chat drawer + trade modal: no focus-trap/`aria-modal`/focus-return | `dashboard.html:46`; `dashboard.js` |
| SEC/DX | CI pip-audit blind to web/auth stack + transitive; pyjwt 2.7.0 & urllib3 CVEs unseen | `ci.yml:59`; `requirements.lock` |
| DX | aiohttp drift 3.13.4 (pyproject) vs 3.14.1 (everything else) | `pyproject.toml:19` |
| SEO | `robots.txt` + `sitemap.xml` absent | — |
| DX | `/version` missing (no version constant exists) | — |
| TEST | `smart_exits.py` (693 LOC) & `nlp/sanitize.py` (security) untested | — |

### 🟠 P2 — medium
No SIGTERM graceful shutdown (`main.py`); polling-only (no webhook); no typing indicator + `/start` blocks on serial exchange calls; `/status` lacks uptime/last-scan/latency/version; `/help` wall-of-text; no `update_id` log correlation; "LIVE — REAL MONEY" badge contrast 3.91:1 (`styles.css:331`); no chat streaming/animated typing; no example/follow-up chips or per-msg copy·regenerate·thumbs; minimal markdown rendering; tap targets < 44px; missing `og:type/url/image:alt`+twitter+canonical; no Python matrix; **zero CI for Node `app/`** (unrun JS test, no `npm audit`); ruff F401 skips `tests/`; cryptography drift; `/signal` singular missing; symbol regex at only ~4 arg entry points; `limit_entry.py`/`exchange_sync.py` untested; **brand-palette brief↔code mismatch (decision)**.

### 🟡 P3 — polish / hygiene
Handlers all group 0; mixed plain+JSON loggers; `threading.Lock` in async limiter; no "More →" pagination; chat input safe-area; `og:image:alt`; voice input; complete ARIA tab pattern; `noindex` on dashboard; `requirements.lock` mislabeled; dev-tool pins float; no pytest markers; ~45 stale `claude/*` branches; mypy `--strict` gap (~1,558, aspirational); orphaned core modules untested; ~900 non-gated ruff style hits.

### 🟢 Strengths (keep / build on)
Clean PTB v20 async (no v13 residue) · `concurrent_updates(True)` + money-path locking · tamper-evident redacted JSON audit logs (SHA-256 chain) · hard allowlist + role gate + injection sanitization · 0 committed secrets, thorough `.env.example`, gitignored `.jwt_secret` · bandit high/high clean, mypy-gated subset clean · ultra-light landing (~4.5 KB gz JS, zero images) · comprehensive `prefers-reduced-motion` + global focus-visible · canonical skeleton/empty/error state machine with sharp on-brand copy · server-side chat persistence · custom CI flake-filter + coverage floor + strict baseline · recent merged safety work (#373–#378: live-PnL breakers, credential hardening, venue safety, `/livebalance` routing, per-user breaker C1, live-auth safe-halt).

---

## Appendix — measured facts
- Python 3.11.15 · PTB 22.7 · 148 bot modules · 331 test files / ~3,545 tests · landing JS ~4.5 KB gz.
- Contrast (WCAG): body 16.96:1 ✅ · gold CTA label 8.89:1 ✅ · LIVE badge 3.91:1 ❌ · (briefed orange+white 2.86:1 ❌).
- pip-audit (full env): pyjwt/urllib3/setuptools/pip/pytest/wheel advisories present; **CI does not see them**.
- bandit high/high: clean · mypy gated subset: clean · mypy --strict bot/: ~1,558.

*End of Deliverable A. Awaiting go-ahead before Deliverable B (ordered fix + improvement plan with before/after diffs). Lead open question for B: brand-palette direction (keep shipped gold vs repaint to briefed ember).*
