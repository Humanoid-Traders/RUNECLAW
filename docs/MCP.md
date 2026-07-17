# RUNECLAW MCP Server

RUNECLAW exposes its **read-only trading intelligence** as a
[Model Context Protocol](https://modelcontextprotocol.io) server, so any
MCP-capable agent — Claude Code, Claude Desktop, agent frameworks — can use
the engine's data as tools.

## Endpoint

```
POST https://<your-deployment>/mcp
```

Transport: **Streamable HTTP, stateless** (plain JSON responses; no SSE
stream, no sessions). Per-IP rate-limited.

### Connect from Claude Code

```bash
claude mcp add --transport http runeclaw https://<your-deployment>/mcp
```

## Scope — read-only by design

Every tool serves data the public website already publishes. **No tool can
access a user account, and no tool can place, modify, or cancel a trade.**
Trade-capable MCP tools are a separate, operator-gated decision that has not
been taken.

## Tools

| Tool | What it returns |
|------|-----------------|
| `get_track_record` | Public verifiable performance: win rate, profit factor, net PnL, recent closed trades — from recorded history |
| `get_signals` | Recent engine-generated signals (taken or not) with confidence, levels, and resolved outcomes |
| `get_agent_feed` | The agent's live mind-stream: scans, theses, opens/closes, stop moves |
| `get_rwa_radar` | Tokenized-RWA sector radar from live venue tickers (volume-weighted, vs-BTC) |
| `get_dex_compare` | DEX↔CEX basis: Hyperliquid mids vs venue perp prices, in bps |
| `get_showcase_trade` | One real recorded trade (biggest recent \|PnL\|, win or loss) |
| `run_what_if` | Hypothetical fixed-stake replay of the recorded history (`stake_usd`, `days`, `symbol`) |
| `get_weekly_letter` | The Agent Letter for the last completed ISO week |

All responses are JSON; hypothetical outputs are labelled hypothetical, and
past performance never predicts future results.
