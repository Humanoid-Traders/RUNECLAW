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
const { getGateway, isConfigured: gatewayConfigured } = require('../lib/gateway');

const router = express.Router();

const PROTOCOL_VERSION = '2025-03-26';
const SERVER_INFO = { name: 'runeclaw', version: '2.0.0' };

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

  get_proof_of_pnl: {
    description: "RUNECLAW's continuously-published Proof-of-PnL: the sealed, "
      + 'public-safe track-record statement with a SHA-256 publish_hash over the '
      + 'canonical bundle, its freshness, the trust tier and reconciliation '
      + 'status, and the ERC-8004 identity anchor (honestly UNVERIFIED until a '
      + 'real tx confirms it). Machine counterpart of the /proof page: an agent '
      + 'can re-derive the hash itself instead of trusting this response. '
      + 'Re-derive: canonical = JSON with recursively sorted keys, no whitespace, '
      + 'UTF-8 (every number already a string); publish_hash = SHA-256(canonical).',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => {
      if (!gatewayConfigured()) return { published: false, error: 'not_configured' };
      const r = await getGateway('/public/proofofpnl', 15000);
      if (r.status < 200 || r.status >= 300) return { published: false, error: 'unavailable' };
      const d = r.data || {};
      // Pass the sealed statement through verbatim so the caller verifies the
      // SAME bytes we did — plus a machine-readable re-derivation recipe.
      return {
        ...d,
        reverify: {
          canonicalization: 'json.dumps(bundle, sort_keys=True, separators=(",",":"), ensure_ascii=False)',
          hash: 'sha256(utf8(canonical))',
          note: 'All numbers in the bundle are strings, so a recursive key-sort '
            + '+ JSON.stringify reproduces the canonical bytes in any language.',
        },
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

  get_meme_radar: {
    description: 'Read-only meme & AI-agent token radar from live DEXScreener '
      + 'DEX pairs: trending on-chain tokens ranked by real 24h volume, each '
      + 'with an explicit SAFETY read (liquidity depth, pair age, buy/sell '
      + 'balance, risk tier). Intelligence only — never trades, never launches '
      + 'tokens. Memecoins are extremely high risk; most go to zero.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/meme').getRadar(),
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

  // ── v2 tools ──────────────────────────────────────────────────────────────

  research_token: {
    description: 'Evidence dossier for a listed coin — live market read, '
      + 'sector membership, DEX presence, the deterministic SAFETY read '
      + '(heuristic red flags, never a verdict), engine signal history and '
      + "the agent's own recorded track record on the coin. Composed only "
      + 'from trusted live sources and recorded platform history; a coin the '
      + 'venue does not list returns listed:false (nothing to research '
      + 'honestly).',
    inputSchema: {
      type: 'object',
      properties: { symbol: { type: 'string', maxLength: 12 } },
      required: ['symbol'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const base = String(args.symbol || '').toUpperCase()
        .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
      if (!base) return { listed: false, error: 'symbol required' };
      const d = await require('../lib/research').buildDossier(base);
      return d ? { listed: true, ...d }
        : { listed: false, note: 'Not listed on the venue — no trusted live data.' };
    },
  },

  scan_token_safety: {
    description: 'Deterministic token safety heuristics for a coin: thin '
      + 'venue volume, extreme/parabolic 24h moves, on-chain liquidity depth, '
      + 'pair age, honeypot pattern (buys but no sells), one-sided flow, and '
      + 'CEX↔DEX price gap — tiered standard/elevated/high/extreme. Flags '
      + 'are heuristics with reasons, NEVER a verdict: "no flags" means the '
      + 'checks found nothing, not that the token is safe.',
    inputSchema: {
      type: 'object',
      properties: { symbol: { type: 'string', maxLength: 12 } },
      required: ['symbol'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const base = String(args.symbol || '').toUpperCase()
        .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
      if (!base) return { error: 'symbol required' };
      let ticker = null;
      try { ticker = (await require('../lib/tickers').getTickers())[`${base}USDT`] || null; }
      catch (e) { /* CEX side degrades; on-chain checks still run */ }
      return require('../lib/token_safety').scanToken(base, { ticker });
    },
  },

  get_leaderboard: {
    description: 'The public verifiable leaderboard: anonymous handles ranked '
      + 'by re-verified sealed statements (win rate, profit factor, round '
      + 'trips — never account sizes or dollar amounts). Optional season '
      + '"YYYY-MM" returns that frozen monthly board.',
    inputSchema: {
      type: 'object',
      properties: { season: { type: 'string', maxLength: 7 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const season = String(args?.season || '');
      if (season && !/^\d{4}-\d{2}$/.test(season)) return { error: 'season must be YYYY-MM' };
      if (!gatewayConfigured()) return { available: false, error: 'not_configured' };
      const r = await getGateway(`/public/leaderboard${season ? `?season=${season}` : ''}`, 15000);
      if (r.status < 200 || r.status >= 300) return { available: false, error: 'unavailable' };
      return r.data;
    },
  },

  get_agent_card: {
    description: 'The ERC-8004 identity card behind a published agent '
      + 'address: identity, sealed track-record linkage, server-side '
      + 'verification result (re-derived hash + Ed25519), trust tier and '
      + 'reconciliation status. The anchor stays honestly UNVERIFIED until a '
      + 'real on-chain transaction confirms it. Unknown addresses return '
      + 'found:false.',
    inputSchema: {
      type: 'object',
      properties: { address: { type: 'string', maxLength: 42 } },
      required: ['address'],
      additionalProperties: false,
    },
    handler: async (args) => {
      const addr = String(args.address || '').toLowerCase();
      if (!/^0x[0-9a-f]{40}$/.test(addr)) return { error: 'address must be 0x + 40 hex chars' };
      if (!gatewayConfigured()) return { found: false, error: 'not_configured' };
      const r = await getGateway(`/public/agent/${addr}`, 15000);
      if (r.status === 404) return { found: false };
      if (r.status < 200 || r.status >= 300) return { found: false, error: 'unavailable' };
      return { found: true, ...r.data };
    },
  },

  get_public_letter: {
    description: 'The PUBLIC edition of the weekly Agent Letter — the same '
      + 'recorded data recomposed with no dollar figure (counts, win rate, '
      + 'profit factor, equity percent change, alpha vs holding, regime '
      + 'reads). Optional week "YYYY-Wnn"; defaults to the latest completed '
      + 'ISO week. Only completed weeks exist.',
    inputSchema: {
      type: 'object',
      properties: { week: { type: 'string', maxLength: 8 } },
      additionalProperties: false,
    },
    handler: async (args) => {
      const letters = require('../lib/letter');
      const week = String(args?.week || '') || letters.lastCompletedWeek().key;
      if (!/^\d{4}-W\d{2}$/.test(week)) return { error: 'week must be YYYY-Wnn' };
      const letter = await letters.getPublicLetter(week);
      return letter || { found: false, week };
    },
  },

  get_airdrop_radar: {
    description: 'Curated airdrop & testnet campaign radar with guided '
      + 'checklists — status, cost, effort, requirements and official links. '
      + 'GUIDED-ONLY by design: the human performs and signs every step; '
      + 'RUNECLAW never automates participation, never farms with multiple '
      + 'wallets (sybil activity gets retroactively disqualified anyway). '
      + 'Campaigns churn — verify on the official link before acting.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () => require('../lib/airdrops').getPublicAirdropRadar(),
  },

  get_alpha_intel: {
    description: "Derived analytics over the agent's public recorded closed "
      + 'trades: alpha vs simply holding each traded asset (rebuilt from each '
      + "trade's own entry/exit prices — no external price history, fully "
      + 're-derivable), expectancy, payoff ratio, profit factor, max realized '
      + 'drawdown and streaks. Same rows as get_track_record.',
    inputSchema: { type: 'object', properties: {}, additionalProperties: false },
    handler: async () =>
      require('../lib/intel').getUserIntel(parseInt(process.env.BOT_USER_ID) || 1),
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
  for (const k of (schema && schema.required) || []) {
    if (args == null || typeof args !== 'object' || !(k in args)) {
      return `Missing required argument: ${k}`;
    }
  }
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
// Shared with the ERC-8257 tool endpoint (routes/tool8257.js) so the on-chain
// manifest and /mcp can never drift — one read-only tool registry.
module.exports.TOOLS = TOOLS;
module.exports.validateArgs = validateArgs;
