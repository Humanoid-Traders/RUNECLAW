# AI-WEBSEARCH — real-time web search in the agent chat

Design note for the live web-search capability added to the RUNECLAW agent
chat. Ship-gated on operator sign-off because it changes the chat's tool
surface and adds per-query cost.

## Decisions (operator-confirmed)

| Question | Decision |
|---|---|
| **Who can use it** | **Admin / ULTRA only.** It bills per search against the operator's Anthropic key, and only the admin path is allowed to reach that key. Non-admin and anonymous (public) chat never carry the tool. |
| **When it searches** | **Model-decided.** The tool is attached and the model chooses whether to search, based on whether the answer needs fresh facts. No explicit toggle. |
| **Surfaces** | **Web + Telegram together.** Both surfaces route through the same `_llm_chat` chokepoint, so wiring it there covers both at once. |

## How it works

The web-search tool is Anthropic's server-side `web_search_20260209` (dynamic
filtering). Anthropic runs the searches on their infrastructure inside the same
`messages.create` call — RUNECLAW never fetches pages itself.

```
user question
   └─ _llm_chat (bot/skills/telegram_handler.py)   ← the shared web+TG chokepoint
        • web_search_ok = is_admin and not public
        • only attaches on the operator's ANTHROPIC candidate
        └─ llm_complete(..., web_search=True, citations_out=[])   (bot/llm/provider.py)
             • attaches tools=[{type: web_search_20260209, name: web_search, max_uses: 4}]
               ONLY on supported models (model_supports_web_search)
             • model decides whether to search
             • concatenates all text blocks; collects cited sources
        └─ answer + "🔎 Live web sources:" footer (titles + URLs)
```

### Model gating

`web_search_20260209` is supported on Opus 4.6/4.7/4.8, Sonnet 5 / 4.6, and the
Fable/Mythos 5 flagships (`model_supports_web_search`). On any other model the
tool is simply not attached. If a supported-looking model still rejects the
tool, `llm_complete` **strips it and retries once** — the reply degrades to a
plain (memory-only) answer instead of failing. This mirrors the existing
structured-output strip-and-retry net.

### Citations

Cited sources are collected from the model's text-block citations first (what
it actually used), falling back to the raw `web_search_tool_result` blocks.
They are deduped by URL, capped at 8, and rendered as a "🔎 Live web sources"
footer appended to the answer — identical text on web and Telegram.

## Safety (§4)

- **Admin-key isolation preserved.** The tool only rides the operator's
  Anthropic candidate (`cfg.provider == ANTHROPIC` under `is_admin`). The
  existing admin-only guard on that key is unchanged; non-admin fallback chains
  can never attach it.
- **No paywalled / credential-gated content.** The system directive explicitly
  forbids searching or reproducing paywalled or credential-gated material, in
  line with the standing "never scrape paywalled content / never use user
  credentials on publisher sites" rule.
- **No new secret-leak surface.** Errors continue through the existing chat
  fallback path; the F-15 no-secrets-in-user-text discipline is untouched.
- **Budget guard applies.** The chat daily call/dollar budget guard runs before
  every LLM call, web search included. Each search adds Anthropic's per-search
  charge on top of tokens; the token cost is already estimated and recorded, and
  each search-bearing reply is `audit`-logged (`chat_web_search`) with the
  source count for observability. A dedicated per-search dollar surcharge in
  `/costs` is a fast follow if the volume warrants it.

## Rollout

Admin/ULTRA only means blast radius is the operator's own chat. Widen to paid
tiers later by relaxing the `web_search_ok` gate (e.g. `is_admin or tier in
{PLUS, PRO}`) once real cost is observed — no other change required.

## Tests

`tests/test_web_search_chat.py`: model gate, tool attach/no-attach, off-by-
default, multi-block text concatenation, citation collection + dedupe, the
strip-and-retry net, unrelated-error passthrough, and the admin-only source
gate in `_llm_chat`.
