# OKX AI — Analysis-Only MCP Integration Plan

> **Status: DRAFT for review. No code changed by this document.**
> Scope is deliberately **read-only / analysis-only**: expose RUNECLAW's existing
> market-analysis, risk-evaluation, and backtest capabilities to the OKX AI agent
> marketplace over the Model Context Protocol — **without** exposing trade
> execution, without touching the Bitget execution path, and without adding any
> wallet / on-chain / stablecoin-settlement surface.

## 1. Objective

Let agents on **OKX AI** (and any MCP-compatible client — Claude Code, Codex,
Hermes, OpenClaw) call RUNECLAW as a **signal / analysis / safety service**:

- scan the market and rank setups,
- analyze a symbol into a structured trade *idea* (not an order),
- run the 21-check **RUNECLAW Shield** risk evaluation on an arbitrary proposal,
- query macro-event risk state,
- run a synthetic backtest.

RUNECLAW stays a Bitget bot. OKX AI just gets a **read-only window** into its
brain. Nothing here can place, size, or confirm a live trade.

## 2. Why this is low-risk (current state)

The analysis surface is **already built and already execution-free**. `bot/mcp/server.py`
exposes a `TOOL_CATALOGUE` of nine tools, and execution is *intentionally*
excluded — verbatim from the source:

> `# SECURITY: runeclaw_execute intentionally excluded from MCP. Exposing trade
> execution over MCP would bypass the human-confirmation gate, violating the
> fail-closed design.`

Every catalogued tool is read-only:

| MCP tool | Skill | Read-only? | Notes |
|---|---|---|---|
| `runeclaw_scan` | `scan_market` | ✅ | top movers / volume anomalies |
| `runeclaw_analyze` | `analyze_asset` | ✅ | produces a `TradeIdea`, never executes |
| `runeclaw_risk` | `check_risk` | ✅ | drawdown / circuit-breaker status |
| `runeclaw_portfolio` | `get_portfolio` | ✅ | **paper** portfolio only |
| `runeclaw_explain` | `explain_trade` | ✅ | explains an idea |
| `runeclaw_macro` | `macro_calendar` | ✅ | macro-event risk state |
| `runeclaw_shield` | `_shield_evaluate` | ✅ | 21 fail-closed checks → verdict |
| `runeclaw_fullscan` | `_fullscan` | ✅ | 67-symbol ranked scan |
| `runeclaw_backtest` | `run_backtest` | ✅ | synthetic data only |

It already has: bearer-token auth (fail-closed — refuses to start without
`MCP_AUTH_TOKEN`), strict symbol validation, bounds clamping (`bars`≤5000, `mode`
allow-list), structured error envelopes, and secret-redacted tracebacks.

**The gap is transport, not capability.** `RuneClawMCPServer.call_tool(...)` is an
in-process Python method (only caller today: `live_e2e_test.py`). The official
`mcp` SDK is **not installed**. So no external client can connect yet. That is the
single thing this plan adds.

## 3. Architecture

```
                       ┌─────────────────────────────────────┐
  OKX AI / Onchain OS  │  RUNECLAW MCP transport adapter      │
  (or Claude Code,     │  bot/mcp/transport.py  [NEW]         │
   Codex, Hermes) ─────┼──▶ official `mcp` SDK Server         │
   MCP over stdio /    │      • list_tools()  ──┐             │
   streamable-HTTP     │      • call_tool()  ───┤             │
                       │                        ▼             │
                       │   RuneClawMCPServer (EXISTING)       │
                       │   bot/mcp/server.py                  │
                       │      read-only TOOL_CATALOGUE        │
                       │            │                         │
                       │            ▼                         │
                       │   SkillRegistry  →  RuneClawEngine   │
                       │   (read paths only; no executor)     │
                       └─────────────────────────────────────┘
```

The adapter is **thin**: it maps the MCP SDK's `@server.list_tools()` /
`@server.call_tool()` handlers onto the existing `list_tools()` / `call_tool()`
methods and re-uses the existing auth + validation. No business logic moves.

### Analysis-only enforcement (defense in depth)

1. **Catalogue allow-list** — the adapter serves *only* `TOOL_CATALOGUE`; there is
   no code path from MCP to `confirm_trade` / `LiveExecutor`.
2. **New invariant test** — assert no MCP tool name or skill resolves to an
   execution-capable skill (`ExecutePaperTradeSkill`, anything routing to the
   executor), so a future catalogue edit can't silently add an execute path.
3. **Engine in read-only posture** — the adapter constructs the engine without
   live credentials / without arming the executor (analysis + paper only).
4. **Keep `MCP_ALLOW_EXECUTE` permanently unset** in this deployment and add a
   startup assertion that it is falsey when serving to OKX AI.

## 4. Work breakdown (sequenced, each a gated PR)

Following the house style: each step ships as its own draft PR off `main`, gated
/ additive, CI gate green, no change to existing behaviour when the new server
isn't launched.

**PR 1 — Transport adapter (additive, opt-in).**
- Add `mcp` (official Python SDK) to `requirements*.txt`.
- `bot/mcp/transport.py`: wrap `RuneClawMCPServer` in an SDK `Server`; stdio
  transport first (what Claude Code / Codex / most MCP clients use).
- `python -m bot.mcp.transport` entrypoint; reads `MCP_AUTH_TOKEN` from env.
- Pure addition — no existing module changes. Byte-identical when not launched.

**PR 2 — Analysis-only invariant + auth plumbing.**
- Thread the SDK's auth/metadata into the existing `auth_token` check (the SDK
  carries auth differently per transport — map it to the existing `hmac` compare).
- Add the "no execution path reachable from MCP" invariant test (§3.2).
- Add the `MCP_ALLOW_EXECUTE must be unset` startup assertion.

**PR 3 — Streamable-HTTP transport (if OKX AI requires a hosted endpoint).**
- Add the SDK's streamable-HTTP/SSE transport behind a flag, for a
  network-reachable endpoint (OKX AI's A2MCP pay-per-call model likely needs
  this rather than stdio). Bind localhost by default; document a reverse-proxy.
- Rate-limit per token (reuse the bounds-guard mindset).

**PR 4 — OKX AI registration manifest + docs.**
- A `okx-ai/` manifest describing the service + the nine tools (their existing
  JSON Schemas come straight from `list_tools()`), pricing/identity fields per
  OKX AI's spec, and a runbook for registering via Onchain OS.
- README/`docs` section: how to launch, how to point an MCP client at it.

**PR 5 — (optional) `agent-skills` PR to okx/agent-skills.**
- Separately, package `runeclaw_shield` and `runeclaw_analyze` as OKX's
  Markdown+YAML skill format (≤1024-char descriptions) so they're discoverable in
  their skills marketplace. This fronts the *analysis*, not Bitget execution.

## 5. Security & risk posture

- **No execution, ever** — enforced by allow-list + invariant test + engine
  posture + `MCP_ALLOW_EXECUTE` assertion (four independent layers).
- **No secrets exposed** — server already redacts tracebacks; the read-only tools
  return market analysis / paper state, not credentials. Audit: confirm
  `get_portfolio` / `check_risk` emit only paper/aggregate figures, never API keys
  or live balances, before exposing externally.
- **Auth required** — fail-closed `MCP_AUTH_TOKEN`; for a public OKX AI endpoint,
  issue a dedicated token (rotation runbook) distinct from any internal token.
- **No wallet / on-chain surface** — explicitly out of scope. OKX AI's agentic
  wallet / stablecoin settlement / A2A escrow are **not** integrated; we offer a
  callable MCP service only. (A paid A2MCP listing may need a receiving address —
  that is a separate decision with its own risk review, **not** in this plan.)
- **Risk engine untouched** — Shield calls `risk.evaluate()` read-only; it returns
  a verdict, it does not size or place anything.

## 6. Testing & CI

- Unit: adapter maps `list_tools`/`call_tool` faithfully; auth rejects bad tokens;
  the analysis-only invariant holds.
- Integration: spin the stdio server in-process, drive it with the MCP SDK client,
  assert each of the nine tools returns a well-formed envelope; assert there is no
  tool whose name or skill can execute.
- `python scripts/ci_test_gate.py` must show **PASS — no new failures**.
- Keep everything additive so the baseline stays byte-identical when the server
  isn't running.

## 7. Open questions (need OKX-side specifics before PR 3–5)

1. **Transport OKX AI expects** — stdio vs hosted streamable-HTTP/SSE. Drives PR 3.
2. **Identity/registration** — does listing *require* an OKX Agentic Wallet / on-chain
   identity even for a free, read-only service? If yes, that crosses our "no wallet
   surface" line and needs an explicit decision.
3. **Monetization** — free listing vs A2MCP pay-per-call. Paid = a receiving
   address = new surface = separate risk review.
4. **Hosting** — RUNECLAW currently runs as a Telegram bot process; a public MCP
   endpoint needs a reachable host + TLS + the reverse proxy in PR 3.
5. **OKX ToS / data** — confirm exposing analysis derived from Bitget data via an
   OKX-hosted marketplace is within both venues' terms.

## 8. Recommended first step

**Ship PR 1 + PR 2 only** (stdio transport + analysis-only invariant). That makes
RUNECLAW connectable from any local MCP client *today* (Claude Code, Codex), proves
the surface end-to-end, and is fully reversible — all **before** committing to the
OKX-hosted-endpoint and identity questions in §7, which depend on OKX AI specifics
we don't yet have. Hold PR 3–5 until those are answered.

---

*Companion to `docs/FLAG_ACTIVATION.md` and the MCP server at `bot/mcp/server.py`.*
