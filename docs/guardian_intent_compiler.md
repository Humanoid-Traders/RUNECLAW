# Guardian — Formal Strategy Intent Compiler

> The AI proposes. Deterministic controls authorize. The wallet enforces. The recorder proves.

The Intent Compiler turns a plain-language trading intent into a **compiled
policy**: a versioned, content-hashed list of typed rules that the risk gate
enforces **deterministically** — no LLM at enforcement time. It is the second
Guardian module, building on the Agent Flight Recorder.

## The guarantees

1. **A policy may only TIGHTEN, never loosen.** Two independent guarantees:
   - *Compile-time clamp* — every cap-mirroring rule is clamped against the
     engine's authoritative `CONFIG.risk.*` cap, so a looser-than-cap value can't
     even exist in the artifact.
   - *Control-flow property* — the enforcement hook's only power is to append to
     the risk gate's `failed` list, and the engine computes
     `verdict = APPROVED iff len(failed) == 0`. Appending can only flip
     APPROVED→REJECTED, never the reverse. Tighten-only is proven, not asserted.
2. **Deterministic + fail-open.** Enforcement is a pure function of the trade and
   the policy. A rule that can't be evaluated (missing runtime data, malformed
   value) is *skipped*, never crashed — the engine's own 23 caps remain the floor,
   so a policy bug can never halt trading.
3. **Off by default.** With `INTENT_POLICY_ENABLED` unset/false the hook is
   skipped entirely — byte-identical to before.
4. **Shadow-first.** Each policy carries a `mode`: `shadow` records would-reject
   violations without blocking; `enforce` actually rejects. Run shadow in
   production first, read the observations on the Guardian page, then switch.

## Authoring a policy (operator, shadow-first)

1. Copy `config/intent_policy.example.json` → `config/intent_policy.json`.
2. Edit the `rules` (see the registry below). Keep `"mode": "shadow"` to start.
3. Set `INTENT_POLICY_ENABLED=true` and restart (or call
   `engine.reload_intent_policy()`).
4. Watch the **Guardian** page: the active policy + its rules show at the top,
   and every decision's `POLICY_DECISION` record is sealed into the Flight
   Recorder ledger.
5. When the shadow log looks right, switch `"mode": "enforce"` and reload.

Natural-language authoring is deterministic: `intent_policy.compile_nl()` parses
common phrasings ("only majors, max 5% per trade, no shorts, cap 3 open, min
confidence 70%") into a rule list you review before it compiles. The LLM only ever
*drafts* — the compiled artifact is what enforces. (Web/Telegram authoring UI is
a follow-up; PR-2 ships operator-file authoring + read-only surfaces.)

## Rule registry (MVP)

| type | params | violates when | clamps against |
|---|---|---|---|
| `max_position_pct` | number `%` | `position_usd / equity` > limit | `max_position_pct` |
| `max_open_positions` | integer | effective open count ≥ limit | `max_open_positions` |
| `min_confidence` | 0–1 | idea confidence < floor | `min_confidence` |
| `min_rr` | number `R` | reward:risk < floor | `min_risk_reward` |
| `max_daily_loss_pct` | number `%` | daily loss > limit | `max_daily_loss_pct` |
| `max_drawdown_pct` | number `%` | drawdown > limit | `max_drawdown_pct` |
| `allowed_symbols` | `[base…]` | asset not in the allowlist | — (adds restriction) |
| `blocked_symbols` | `[base…]` | asset in the blocklist | — |
| `allowed_strategy_types` | `[str…]` | strategy not in the allowlist | — |
| `direction` | `long_only`/`short_only` | trade is the wrong side | — |

> `max_position_pct` uses the engine's own name and meaning: the compared value
> is `position_usd / equity` — the engine calls that `position_pct` and caps it
> with `CONFIG.risk.max_position_pct`. (`position_usd` is committed as margin, so
> this is position size as % of equity, not a leverage-adjusted notional or a
> risk-to-stop figure.) A true `max_risk_pct` (over stop distance), policy-level
> exposure/free-margin rules, and a per-trade leverage rule (live leverage is a
> fixed config today, not per-trade) are deferred to a follow-up.

## Where it plugs in

- **Compile/evaluate:** `bot/guardian/intent_policy.py` (pure).
- **Enforcement:** `bot/risk/risk_engine.py` — one gated block just before the
  verdict is derived; `RiskEngine.set_intent_policy()` binds the compiled policy.
- **Load:** `bot/core/engine.py` `_load_intent_policy_onto()` compiles the
  on-disk policy against live caps at boot; `reload_intent_policy()` hot-reloads.
- **Evidence:** every consultation seals a `POLICY_DECISION` event on the
  tamper-evident Flight Recorder chain, and the `INTENT_POLICY:` verdict rides in
  the RiskCheck.
- **Surface:** the bot pushes a read-only policy summary to the web; the Guardian
  dashboard shows the active policy, and `/api/guardian/flight` returns it.

## Config

| env | default | effect |
|---|---|---|
| `INTENT_POLICY_ENABLED` | `false` | master switch — off = hook skipped entirely |
| `INTENT_POLICY_PATH` | `config/intent_policy.json` | operator policy file |

## Authoring from Telegram (PR-2b)

An operator (admin) authors and manages the policy in plain language — the AI
proposes, deterministic controls compile, and nothing binds without an explicit
tap:

| command | effect |
|---|---|
| `/policy` | show the active policy, its mode, and whether enforcement is on |
| `/policy set <plain English>` | `compile_nl` → `compile_policy` → **preview the compiled rules + any cap-clamp warnings** with inline confirm buttons |
| *(tap)* `Apply (shadow)` / `Apply (enforce)` | persist to `config/intent_policy.json` (atomic write) and hot-reload onto the live engine |
| `/policy mode shadow\|enforce\|off` | change the active policy's mode |
| `/policy clear` | remove the policy |

Example: `/policy set only majors, max 5% per trade, no shorts, min confidence 70%`
→ compiles to `allowed_symbols`, `max_position_pct`, `direction: long_only`,
`min_confidence` and shows them for review.

Safety of the authoring path:
- **Admin-gated** command; the apply buttons carry the same `mode` permission as
  a strategy-mode change, and are the *only* place a policy is persisted/bound.
- **Shadow-first** — the preview's primary button applies in shadow (logs
  would-be rejections, blocks nothing). Enforce is the explicit second button.
- **Dormant unless enabled** — with `INTENT_POLICY_ENABLED` off, an authored
  policy is saved but not consulted; the reply says so.
- **Provably one** — a policy's hash/`policy_id` is derived from its *rules*
  (not its label), so the authored artifact and the enforced artifact are
  identical across the write→reload round-trip, and the `POLICY_DECISION` record
  proves which one ran.
