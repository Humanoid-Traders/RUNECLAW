# ULTRA AI — the Claude Fable 5 addon roadmap (AI-1 … AI-5)

Operator directive (2026-07-20): "we need the fable 5 ultra AI addons we can
add to our AI agent — give the best and most valuable options" + "ultra mode".

This document is the ranked plan. AI-1 ships with this change; AI-2…AI-5 are
queued in best-value order. All Anthropic usage stays behind the existing
hard guard: **the operator's Claude key is admin-only** — non-admin users are
never routed to it, regardless of any table, env, or toggle below.

## What Fable 5 is (and costs)

`claude-fable-5` is Anthropic's most capable widely released model
(Mythos-class tier above Opus): 1M-token context, 128K max output,
**thinking always on**. Two API differences matter for our call path:

1. The `thinking` request parameter is **rejected** (it's always on) — depth
   is steered with `output_config.effort` (`low`…`max`) instead. Sending
   `thinking: adaptive` like we do for Opus would 400 every call — the same
   failure shape as the Claude 5 `temperature` deprecation that took the
   brain down to the rule engine on 2026-07-16.
2. Responses can end with `stop_reason: "refusal"` — no usable content. Our
   call path now raises on it so callers fall back to the rule engine
   instead of treating an empty string as an answer.

Pricing: **$10 / $50 per MTok** (input/output) — 2x Opus 4.8, ~3x Sonnet 5.
That is why ULTRA is an explicit opt-in and never a default.

## AI-1 — ULTRA mode (SHIPPED in this change)

| Tier | ULTRA routes to | Why |
|---|---|---|
| THESIS | `claude-fable-5`, effort `high` | The trade-decision brain — deepest reasoning where it pays |
| LEARNING | `claude-fable-5`, effort `max` | Reflection/macro learning is infrequent — max depth is affordable |
| SCAN | `claude-sonnet-5` | High-frequency; Fable here buys latency and cost, not quality |
| CHAT | `claude-sonnet-5` | Responsiveness matters more than depth |

Controls:

- `/ultra on` / `/ultra off` (admin-only, applies to the next analysis via a
  live client refresh; reverts on restart)
- `LLM_ULTRA_ENABLED=1` makes it the boot default
- Enabling **requires** a usable Anthropic key (fail-loud at set time, same
  rule as `/settier`) — an ULTRA that silently falls back to cheap routing
  would misrepresent what the operator is paying for.

Also in AI-1, for every Anthropic call regardless of ULTRA:

- Fable/Mythos-safe parameters (no `thinking` param; `output_config.effort`
  when a route sets one)
- `stop_reason: "refusal"` → raise → existing rule-based fallback
- `/settier` and `/ultra` now refresh the analyzer's cached **admin** tier
  clients too (previously only set at init, so runtime routing changes
  didn't reach the scan/thesis brain until restart).

## Queued next, in best-value order

- **AI-2 — Structured outputs** (`output_config.format`, strict tools): the
  thesis/voter JSON becomes schema-constrained on Claude 5 calls —
  eliminates the parse-failure class in the 60/40 LLM blend. Keep the
  tolerant parser for non-Anthropic providers.
- **AI-3 — Batches API (50% cost)**: weekly agent letter, scheduled dossier
  refreshes, and shadow-A/B replays are not latency-sensitive — run them
  through `/v1/messages/batches` at half price.
- **AI-4 — Server-side web search + citations**: `web_search` server tool +
  cited document blocks for `/research` dossiers and the news pipeline —
  claims arrive with sources attached (aligns with the §4 reconstructibility
  principle). Admin/ultra path only.
- **AI-5 — Vision**: admin sends a chart or position screenshot to chat; the
  Claude path reads it (the 2026-07-20 BCH 20x incident was diagnosed from
  exactly such a screenshot — the agent should be able to do that itself).

Deliberately NOT queued: server-side fallbacks on refusal (requires 30-day
data retention on the org — operator decision, not a code default), and any
non-admin routing to Anthropic (hard guard stays).
