/**
 * RUNECLAW MCP server — the agent-hub interface.
 *
 * A minimal, dependency-free Model Context Protocol server (Streamable HTTP,
 * stateless JSON responses) that lets ANY MCP-capable agent — Claude, agent
 * frameworks, other bots — consume RUNECLAW's intelligence as tools.
 *
 * Scope is deliberate: every tool is READ-ONLY and serves data this site
 * already publishes without auth (public track record, signal stream, agent
 * feed, RWA radar, DEX comparison, showcase trade, what-if replay over the
 * public history, the weekly letter derived from that same public data).
 * No tool can see a user's account, and no tool can act — trade-capable MCP
 * tools are a separate, gated decision for the operator.
 *
 * Protocol: JSON-RPC 2.0 over POST /mcp (MCP Streamable HTTP transport,
 * stateless mode — plain JSON responses, no SSE stream, no sessions).
 * GET/DELETE answer 405, per spec for servers that don't offer a stream.
 */

const express = require('express');
const { pool } = require('../db');
const { getLatestFlight } = require('./sync');

const router = express.Router();

const PROTOCOL_VERSION = '2025-03-26';
const SERVER_INFO = { name: 'runeclaw', version: '1.0.0' };

// Per-IP limiter: MCP is public surface. Uses the shared limiter (periodic
// idle-bucket pruning) — the earlier hand-rolled map never expired entries
// and, once full, evicted the OLDEST-INSERTED key, which could reset an
// actively-limited IP's counter under bucket churn.
const { rateLimit, ipKey } = require('../lib/rate_limit');
router.use(rateLimit({ windowMs: 60_000, max: 60, key: ipKey, message: 'rate_limited' }));

// The global express.json() (1 MB) has already parsed the body by the time
// the per-route 64 KB parser runs, making that cap a no-op. Enforce the
// intended MCP payload bound explicitly.
const MAX_MCP_BODY_BYTES = 64 * 1024;
router.use((req, res, next) => {
  const len = parseInt(req.headers['content-length'] || '0', 10);
  if (len > MAX_MCP_BODY_BYTES) {
    return res.status(413).json({ error: 'Payload too large (64 KB max)' });
  }
  next();
});

// ── Tool registry ────────────────────────────────────────────────────────────
// Each tool: { description, inputSchema, handler(args) -> JSON-serializable }.
// Handlers reuse the exact libraries behind the public site — no new data
// paths, no new exposure.

const TOOLS = {
  get_track_record: {
    description: "RUNECLAW's public verifiable track record: closed-trade "
      + 'stats (win rate, profit factor, net PnL, drawdown), monthly PnL, '
      + 'equity curve and recent trades — all from recorded history, nothing '
      + 'hand-entered.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      // Reuse the public route's aggregation by querying the same tables it
      // does — via an internal fetch to keep one source of truth.
      const [trades] = await pool.execute(
        `SELECT symbol, direction, pnl, fees, opened_at, closed_at
           FROM trades WHERE user_id = ? AND status = 'CLOSED'
            AND closed_at IS NOT NULL ORDER BY closed_at ASC`,
        [parseInt(process.env.BOT_USER_ID) || 1]);
      const pnls = trades.map(t => parseFloat(t.pnl) || 0);
      const wins = pnls.filter(p => p > 0);
      const grossWin = wins.reduce((a, b) => a + b, 0);
      const grossLoss = Math.abs(pnls.filter(p => p < 0).reduce((a, b) => a + b, 0));
      return {
        trades: trades.length,
        wins: wins.length,
        win_rate_pct: trades.length ? Math.round(wins.length / trades.length * 10000) / 100 : null,
        net_pnl_usd: Math.round(pnls.reduce((a, b) => a + b, 0) * 100) / 100,
        profit_factor: grossLoss > 0 ? Math.round(grossWin / grossLoss * 100) / 100 : null,
        recent_trades: trades.slice(-10).reverse().map(t => ({
          symbol: t.symbol, direction: t.direction,
          pnl: Math.round((parseFloat(t.pnl) || 0) * 100) / 100, closed_at: t.closed_at,
        })),
        source: 'recorded closed trades (same data as the public /track page)',
      };
    },
  },

  get_signals: {
    description: 'The most recent trade signals the engine generated — taken '
      + 'or not — with direction, confidence, pattern, entry/stop/target and '
      + 'resolved outcome where known.',
    inputSchema: {
      type: 'object',
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      const [rows] = await pool.execute(
        `SELECT signal_key, symbol, direction, confidence, pattern, regime,
                entry_price, stop_loss, take_profit, rr, status, pnl, created_at
           FROM signals ORDER BY created_at DESC LIMIT ${limit}`, []);
      return { signals: rows };
    },
  },

  get_flight_record: {
    description: 'Guardian Flight Recorder: the tamper-evident ledger of the '
      + "agent's recent trading decisions. Each record carries full provenance — "
      + 'the reasoning, ranked voter contributions, LLM model/prompt version, the '
      + 'risk-gate verdict, and the realised outcome (PnL/close) — plus the '
      + 'engine-verified SHA-256 hash-chain status proving the log is unaltered.',
    inputSchema: {
      type: 'object',
      properties: {
        limit: { type: 'integer', minimum: 1, maximum: 50 },
        decision_id: { type: 'string', description: 'Return only this decision' },
      },
      additionalProperties: false,
    },
    handler: async (args) => {
      const flight = await getLatestFlight();
      const all = (flight && Array.isArray(flight.records)) ? flight.records : [];
      const chain = (flight && flight.chain) || {};
      if (args && args.decision_id) {
        const rec = all.find((r) => r && r.decision_id === args.decision_id);
        return { record: rec || null, chain };
      }
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      return {
        chain: {
          verified: chain.ok !== false,
          entries: chain.length ?? null,
          tip_hash: chain.tip_hash ?? null,
        },
        records: all.slice(0, limit),
        source: 'engine-side hash-chained audit ledger (logs/audit_chain.jsonl)',
      };
    },
  },

  get_agent_feed: {
    description: "The agent's public mind-stream: recent scans, trade theses, "
      + 'opens/closes, stop moves and alerts, as emitted live by the engine.',
    inputSchema: {
      type: 'object',
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const limit = Math.min(parseInt(args?.limit) || 20, 50);
      const [rows] = await pool.execute(
        `SELECT event_type, severity, symbol, title, body, created_at
           FROM agent_events ORDER BY id DESC LIMIT ${limit}`, []);
      return { events: rows };
    },
  },

  get_rwa_radar: {
    description: 'Read-only tokenized real-world-asset sector radar from live '
      + 'venue tickers: RWA platforms, RWA-narrative chains and RWA-adjacent '
      + 'DeFi with volume-weighted 24h change and sector-vs-BTC read.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/rwa').getRadar(),
  },

  get_dex_compare: {
    description: 'DEX↔CEX basis: live Hyperliquid mid prices for the majors '
      + "against this venue's perpetual prices, in bps. Read-only public data.",
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/dex').getDexCompare(),
  },

  get_showcase_trade: {
    description: 'One real recorded trade (the largest |PnL| close of the '
      + 'last 14 days, win or loss) — the same pick the landing page animates. '
      + 'Null when there is nothing real to show.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      // Same pick logic as /api/public/replay-trade, via its module cache.
      const [rows] = await pool.execute(
        `SELECT symbol, direction, entry_price, exit_price, size_usd, pnl,
                opened_at, closed_at
           FROM trades WHERE user_id = ? AND status = 'CLOSED'
            AND closed_at IS NOT NULL ORDER BY closed_at ASC`,
        [parseInt(process.env.BOT_USER_ID) || 1]);
      const usable = rows.filter(t => isFinite(parseFloat(t.pnl)));
      if (!usable.length) return { trade: null };
      const cutoff = Date.now() - 14 * 86_400_000;
      const recent = usable.filter(t => new Date(t.closed_at).getTime() >= cutoff);
      const src = recent.length ? recent : [usable[usable.length - 1]];
      const pick = src.reduce((a, b) =>
        Math.abs(parseFloat(b.pnl)) > Math.abs(parseFloat(a.pnl)) ? b : a);
      return { trade: pick };
    },
  },

  run_what_if: {
    description: 'Hypothetical replay: what if every recorded agent trade had '
      + 'been mirrored with a fixed stake? Real recorded entries/exits/fees, '
      + 'scaled — never simulated. Always labelled hypothetical.',
    inputSchema: {
      type: 'object',
      properties: {
        stake_usd: { type: 'number', minimum: 10, maximum: 1000000 },
        days: { type: 'integer', minimum: 0, maximum: 3650 },
        symbol: { type: 'string', maxLength: 12 },
      },
      additionalProperties: false,
    },
    handler: async (args) => ({
      hypothetical: true,
      ...(await require('../lib/replay').runReplay({
        stake: args?.stake_usd || 1000,
        days: args?.days || 0,
        symbol: args?.symbol || '',
      })),
    }),
  },

  get_weekly_letter: {
    description: "The Agent Letter — the weekly fund-style letter composed "
      + 'entirely from recorded data (trades, equity, signal flow). Latest '
      + 'completed ISO week; generated on first request.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      const letters = require('../lib/letter');
      const r = await letters.getLetter(letters.lastCompletedWeek());
      return r.letter;
    },
  },
};

// ── JSON-RPC plumbing ────────────────────────────────────────────────────────

function rpcResult(id, result) { return { jsonrpc: '2.0', id, result }; }
function rpcError(id, code, message) {
  return { jsonrpc: '2.0', id: id ?? null, error: { code, message } };
}

async function handleRpc(msg) {
  if (!msg || msg.jsonrpc !== '2.0' || typeof msg.method !== 'string') {
    return rpcError(msg && msg.id, -32600, 'Invalid request');
  }
  const { id, method, params } = msg;

  if (method === 'initialize') {
    return rpcResult(id, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: SERVER_INFO,
      instructions: 'RUNECLAW read-only trading intelligence. Every tool serves '
        + 'data the public site already publishes; no tool can access accounts '
        + 'or place trades. Past performance never predicts future results.',
    });
  }
  if (method === 'notifications/initialized' || method.startsWith('notifications/')) {
    return null;                                     // notification → no body
  }
  if (method === 'ping') return rpcResult(id, {});
  if (method === 'tools/list') {
    return rpcResult(id, {
      tools: Object.entries(TOOLS).map(([name, t]) => ({
        name,
        description: t.description,
        inputSchema: t.inputSchema,
        annotations: { readOnlyHint: true, openWorldHint: false },
      })),
    });
  }
  if (method === 'tools/call') {
    const name = params && params.name;
    const tool = TOOLS[name];
    if (!tool) return rpcError(id, -32602, `Unknown tool: ${name}`);
    const argErr = validateArgs(tool.inputSchema, params.arguments);
    if (argErr) return rpcError(id, -32602, argErr);
    try {
      const out = await tool.handler(params.arguments || {});
      return rpcResult(id, {
        content: [{ type: 'text', text: JSON.stringify(out) }],
        isError: false,
      });
    } catch (e) {
      return rpcResult(id, {
        content: [{ type: 'text', text: `Tool failed: ${String(e.message || e).slice(0, 200)}` }],
        isError: true,
      });
    }
  }
  return rpcError(id, -32601, `Method not found: ${method}`);
}

/**
 * Enforce each tool's declared inputSchema before dispatch (previously the
 * schema was advertised but arguments went to handlers unvalidated). Minimal
 * on purpose — object shape, known keys, primitive types, string caps —
 * matching the simple schemas this server declares.
 */
function validateArgs(schema, args) {
  if (args == null) return null;
  if (typeof args !== 'object' || Array.isArray(args)) return 'arguments must be an object';
  const props = (schema && schema.properties) || {};
  for (const [k, v] of Object.entries(args)) {
    const spec = props[k];
    if (!spec) {
      if (schema && schema.additionalProperties === false) return `Unknown argument: ${k}`;
      continue;
    }
    if (spec.type === 'string') {
      if (typeof v !== 'string') return `${k} must be a string`;
      if (v.length > 200) return `${k} too long (200 max)`;
    } else if (spec.type === 'number' || spec.type === 'integer') {
      if (typeof v !== 'number' || !isFinite(v)) return `${k} must be a number`;
      if (spec.type === 'integer' && !Number.isInteger(v)) return `${k} must be an integer`;
    } else if (spec.type === 'boolean' && typeof v !== 'boolean') {
      return `${k} must be a boolean`;
    }
  }
  return null;
}

router.post('/', express.json({ limit: '64kb' }), async (req, res) => {
  try {
    const out = await handleRpc(req.body);
    if (out === null) return res.status(202).end();  // notification accepted
    res.json(out);
  } catch (err) {
    res.status(500).json(rpcError(null, -32603, 'Internal error'));
  }
});

// Stateless server: no SSE stream to open, no sessions to delete.
router.get('/', (req, res) => res.status(405).json({ error: 'No stream — POST JSON-RPC to this endpoint' }));
router.delete('/', (req, res) => res.status(405).json({ error: 'Stateless server' }));

module.exports = router;
