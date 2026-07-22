/**
 * Bot -> Website data sync endpoint.
 * The Telegram bot calls this to push real portfolio & trade data.
 * Authenticated via a shared secret (BOT_SYNC_SECRET).
 */

const express = require('express');
const crypto = require('crypto');
const { pool } = require('../db');
const { broadcast } = require('./stream');

const router = express.Router();

// Best-effort nudge to connected dashboards -- never let a broadcast issue
// affect the actual sync response (the bot's write already succeeded).
function nudge(type, data) {
  try { broadcast(type, data); } catch (e) { /* non-fatal */ }
}

// Dedupe key for the most recently notified closed trade. The full-replace
// POST / sync fires on every portfolio sync, not just on a fresh close, so
// without this a "trade" SSE event (and therefore a browser notification)
// would re-fire for the same already-seen close every time the bot re-syncs.
let lastNotifiedClose = null;

// CRITICAL: No fallback secret. Refuse to serve sync if unset.
const SYNC_SECRET = process.env.BOT_SYNC_SECRET;
if (!SYNC_SECRET || SYNC_SECRET.length < 32) {
  console.error('WARNING: BOT_SYNC_SECRET must be set (>=32 chars) for sync endpoints to work.');
  console.error('Generate one: node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'hex\'))"');
  // Don't crash the server — sync routes will just reject all requests
}

// Authorized bot user ID: only this user's data can be written via sync.
// In a single-operator deployment, the bot always syncs as user 1.
const AUTHORIZED_BOT_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

// -- In-memory stores (persist within same cold start) --
let latestScan = null;
let latestPortfolio = null; // { equity, open_count, net_pnl, total_trades, win_rate, updated_at }
// Guardian Flight Recorder: recent joined decision records + engine-verified
// chain status. { records: [...], chain: {ok,length,tip_hash,problems}, updated_at }
let latestFlight = null;

// The deep-scan pattern block only rides /deepscan syncs; a regular /scan (or
// the autonomous cycle's empty push) must NOT wipe the last readout. We carry
// the previous block forward until a fresh one arrives or it ages past this TTL.
const DEEPSCAN_TTL_MS = 6 * 60 * 60 * 1000; // 6h

/**
 * GET /api/bot/sync/scan
 * Dashboard fetches latest scan data (no auth required — data is public market info).
 */
router.get('/scan', async (req, res) => {
  if (latestScan) {
    return res.json({ scan: latestScan });
  }
  // Cold start: try to load from DB
  try {
    const [rows] = await pool.execute('SELECT scan_json, updated_at FROM scan_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].scan_json) {
      latestScan = JSON.parse(rows[0].scan_json);
      return res.json({ scan: latestScan });
    }
  } catch (err) {
    console.error('Scan cache load error:', err.message);
  }
  return res.json({ scan: null, message: 'No scan data yet. Run /scan in Telegram.' });
});

/**
 * GET /api/bot/sync/portfolio-summary
 * Dashboard fetches bot portfolio summary (no auth required — shows synced data).
 * Priority: in-memory cache → scan circuit_breaker → DB fallback
 */
router.get('/portfolio-summary', async (req, res) => {
  // Return cached in-memory summary if available
  if (latestPortfolio) {
    return res.json({ portfolio: latestPortfolio });
  }
  // Try to build from persisted scan data (circuit_breaker has live exchange data)
  if (!latestScan) {
    try {
      const [rows] = await pool.execute('SELECT scan_json FROM scan_cache WHERE id = 1');
      if (rows.length > 0 && rows[0].scan_json) {
        latestScan = JSON.parse(rows[0].scan_json);
      }
    } catch (err) { /* ignore */ }
  }
  const cb = latestScan?.circuit_breaker;
  if (cb && (cb.equity != null || cb.total_trades != null || cb.live_unavailable)) {
    latestPortfolio = {
      // Preserve null when the bot flagged the live account UNAVAILABLE — never
      // coerce it to 0 or a paper baseline (the dashboard renders "—" +
      // "live account unavailable" instead of a fake balance).
      equity: cb.live_unavailable ? null : (cb.equity || 0),
      open_count: cb.open_count || 0,
      net_pnl: cb.net_pnl || 0,
      total_trades: cb.total_trades || 0,
      win_rate: cb.win_rate || 0,
      mode: cb.live_mode ? 'LIVE' : 'PAPER',
      live_unavailable: !!cb.live_unavailable,
      updated_at: latestScan.received_at || latestScan.timestamp || new Date().toISOString()
    };
    return res.json({ portfolio: latestPortfolio });
  }
  // Final fallback: read from DB
  try {
    const [snapRows] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots ORDER BY snapshot_at DESC LIMIT 1'
    );
    const [tradeRows] = await pool.execute(
      "SELECT COUNT(*) as total, COALESCE(SUM(pnl),0) as net_pnl FROM trades WHERE status = 'CLOSED'"
    );
    const [openRows] = await pool.execute(
      "SELECT COUNT(*) as open_count FROM trades WHERE status = 'OPEN'"
    );
    const [winRows] = await pool.execute(
      "SELECT COUNT(*) as wins FROM trades WHERE status = 'CLOSED' AND pnl > 0"
    );
    // No invented balances: with no snapshot and no trades there is simply no
    // portfolio yet — the UI renders a real empty state, not a phantom number.
    const total = tradeRows[0]?.total || 0;
    if (snapRows.length === 0 && total === 0) {
      return res.json({ portfolio: null });
    }
    const equity = snapRows.length > 0 ? parseFloat(snapRows[0].equity) : null;
    const netPnl = parseFloat(tradeRows[0]?.net_pnl || 0);
    const openCount = openRows[0]?.open_count || 0;
    const wins = winRows[0]?.wins || 0;
    const winRate = total > 0 ? (wins / total) * 100 : 0;

    latestPortfolio = {
      equity, open_count: openCount, net_pnl: netPnl,
      total_trades: total, win_rate: winRate,
      updated_at: snapRows[0]?.snapshot_at || new Date().toISOString()
    };
    res.json({ portfolio: latestPortfolio });
  } catch (err) {
    res.json({ portfolio: null });
  }
});

// Auth middleware for bot sync — constant-time comparison
function botAuth(req, res, next) {
  if (!SYNC_SECRET) {
    return res.status(503).json({ error: 'Sync not configured (BOT_SYNC_SECRET unset)' });
  }
  const secret = req.headers['x-bot-secret'];
  const a = Buffer.from(secret || '');
  const b = Buffer.from(SYNC_SECRET);
  // timingSafeEqual THROWS on unequal-length buffers — length-check first so a
  // wrong-length secret returns a clean 403 instead of crashing to a 500.
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    return res.status(403).json({ error: 'Invalid bot secret' });
  }
  next();
}

router.use(botAuth);

/**
 * POST /api/bot/sync
 * Body: {
 *   equity: number,
 *   positions: [{ symbol, direction, entry_price, size_usd, fees, pattern, stop_loss, take_profit, opened_at }],
 *   closed_trades: [{ symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at }]
 * }
 *
 * Replaces all trade data for the authorized bot user. user_id is server-enforced, not client-supplied.
 */
router.post('/', async (req, res) => {
  try {
    const user_id = AUTHORIZED_BOT_USER_ID; // Server-enforced, ignores any client-supplied user_id
    const { equity, positions, closed_trades } = req.body;
    // Truthful equity: the bot sends a real number, or null/absent when the
    // LIVE balance can't be read (bot-side resolve_display_equity ->
    // (None,"unavailable")). NEVER coerce that to 0 or a paper baseline — a
    // fake number under a LIVE header is exactly the bug we're killing.
    const eq = Number.isFinite(equity) ? Number(equity) : null;

    // Clear existing trades and snapshots for this user
    await pool.execute('DELETE FROM trades WHERE user_id = ?', [user_id]);
    // Only replace the equity curve when we actually have a real reading;
    // when equity is unavailable, leave the prior snapshots intact (they age
    // out via the freshness gate) rather than stamping a fake point.
    if (eq !== null) {
      await pool.execute('DELETE FROM equity_snapshots WHERE user_id = ?', [user_id]);
    }

    // Insert closed trades
    if (closed_trades && closed_trades.length > 0) {
      for (const t of closed_trades) {
        await pool.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
          [user_id, t.symbol, t.direction, t.entry_price, t.exit_price,
           t.size_usd, t.pnl, t.fees || 0, t.pattern || null,
           t.opened_at ? new Date(t.opened_at) : new Date(),
           t.closed_at ? new Date(t.closed_at) : new Date()]
        );
      }
    }

    // Insert open positions
    if (positions && positions.length > 0) {
      for (const p of positions) {
        await pool.execute(
          `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, opened_at)
           VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)`,
          [user_id, p.symbol, p.direction, p.entry_price,
           p.size_usd, p.fees || 0, p.pattern || null,
           p.stop_loss || null, p.take_profit || null,
           p.opened_at ? new Date(p.opened_at) : new Date()]
        );
      }
    }

    // Insert equity snapshot only for a real reading (see eq above).
    if (eq !== null) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [user_id, eq, new Date()]
      );
    }

    // Update in-memory portfolio summary
    const closedCount = (closed_trades || []).length;
    const openCount = (positions || []).length;
    const netPnl = (closed_trades || []).reduce((a, t) => a + (parseFloat(t.pnl) || 0), 0);
    const wins = (closed_trades || []).filter(t => parseFloat(t.pnl) > 0).length;
    latestPortfolio = {
      equity: eq, open_count: openCount, net_pnl: netPnl,
      total_trades: closedCount, win_rate: closedCount > 0 ? (wins / closedCount) * 100 : 0,
      updated_at: new Date().toISOString()
    };

    // Notify about a genuinely NEW closed trade, deduped against the last one
    // we already surfaced (this endpoint replaces the whole trade list on
    // every sync, not just when a fresh close happens).
    const lastClosed = (closed_trades && closed_trades.length) ? closed_trades[closed_trades.length - 1] : null;
    if (lastClosed) {
      const key = `${lastClosed.symbol}|${lastClosed.closed_at}|${lastClosed.pnl}`;
      if (key !== lastNotifiedClose) {
        lastNotifiedClose = key;
        nudge('trade', { symbol: lastClosed.symbol, direction: lastClosed.direction, pnl: lastClosed.pnl });
      }
    }
    nudge('portfolio');
    res.json({ ok: true, synced: { closed: closedCount, open: openCount, equity: eq } });
  } catch (err) {
    console.error('Sync error:', err.message);
    res.status(500).json({ error: 'Sync failed' });
  }
});

/**
 * POST /api/bot/trade-event
 * Called by the bot when a single trade opens or closes.
 * Body: { event: "open"|"close", trade: {...}, equity }
 */
router.post('/trade-event', async (req, res) => {
  try {
    const user_id = AUTHORIZED_BOT_USER_ID; // Server-enforced
    const { event, trade, equity } = req.body;
    if (!event || !trade) {
      return res.status(400).json({ error: 'event and trade required' });
    }

    if (event === 'open') {
      await pool.execute(
        `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit)
         VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)`,
        [user_id, trade.symbol, trade.direction, trade.entry_price,
         trade.size_usd, trade.fees || 0, trade.pattern || null,
         trade.stop_loss || null, trade.take_profit || null]
      );
    } else if (event === 'close') {
      // Remove from open, add as closed
      await pool.execute(
        "DELETE FROM trades WHERE user_id = ? AND symbol = ? AND status = 'OPEN' LIMIT 1",
        [user_id, trade.symbol]
      );
      await pool.execute(
        `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
        [user_id, trade.symbol, trade.direction, trade.entry_price, trade.exit_price,
         trade.size_usd, trade.pnl, trade.fees || 0, trade.pattern || null,
         trade.opened_at ? new Date(trade.opened_at) : new Date(),
         trade.closed_at ? new Date(trade.closed_at) : new Date()]
      );
    }

    // Record equity snapshot only for a real reading — never a coerced
    // 0/undefined when the live balance is unavailable.
    if (Number.isFinite(equity)) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [user_id, Number(equity), new Date()]
      );
    }

    nudge('trade');
    res.json({ ok: true });
  } catch (err) {
    console.error('Trade event error:', err.message);
    res.status(500).json({ error: 'Trade event failed' });
  }
});

// -- In-memory scan data store is declared above (before botAuth) --

/**
 * POST /api/bot/sync/tiers
 * Bot mirrors its membership tiers ({telegram_id, tier} rows) so users.plan
 * follows the bot's tier authority — /set_tier in Telegram is the ONLY way
 * a tier changes; this endpoint just reflects it. (X-Bot-Secret authed.)
 */
const VALID_TIERS = new Set(['basic', 'pro', 'elite', 'admin']);
router.post('/tiers', async (req, res) => {
  try {
    const rows = Array.isArray(req.body?.tiers) ? req.body.tiers.slice(0, 500) : [];
    let updated = 0;
    for (const r of rows) {
      const tgId = String(r?.telegram_id || '').trim();
      const tier = String(r?.tier || '').toLowerCase();
      if (!tgId || !VALID_TIERS.has(tier)) continue;
      const [result] = await pool.execute(
        'UPDATE users SET plan = ? WHERE telegram_id = ?', [tier, tgId]);
      updated += (result && result.affectedRows) || 0;
    }
    res.json({ ok: true, received: rows.length, updated });
  } catch (err) {
    console.error('Tier sync error:', err.message);
    res.status(500).json({ error: 'Tier sync failed' });
  }
});

/**
 * POST /api/bot/sync/reports
 * Bot pushes the hourly intelligence reports payload (funding scan, arb
 * paper tracker, parity headline, yield radar) built by bot/core/web_reports.
 * Single-row cache like scan_cache. The yield section is operator-sensitive —
 * the read side (routes/reports.js) only serves it to admin-plan users.
 */
let latestReports = null;
router.post('/reports', async (req, res) => {
  try {
    latestReports = { ...req.body, received_at: new Date().toISOString() };
    try {
      await pool.execute(
        'REPLACE INTO reports_cache (id, reports_json) VALUES (1, ?)',
        [JSON.stringify(latestReports)]);
    } catch (dbErr) {
      console.error('Reports cache write error:', dbErr.message);
    }
    nudge('reports');
    res.json({ ok: true });
  } catch (err) {
    console.error('Reports sync error:', err.message);
    res.status(500).json({ error: 'Reports sync failed' });
  }
});
// Read-side accessor for routes/reports.js: in-memory first, DB on cold start.
async function getLatestReports() {
  if (latestReports) return latestReports;
  try {
    const [rows] = await pool.execute(
      'SELECT reports_json FROM reports_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].reports_json) {
      latestReports = JSON.parse(rows[0].reports_json);
    }
  } catch (err) { /* cold-start miss is fine */ }
  return latestReports;
}

/**
 * GET /api/bot/sync/stance/pending + POST /api/bot/sync/stance/ack
 * Round trip for the admin-queued GLOBAL stance change (routes/controls.js
 * queues it; the bot pulls, re-verifies the requester's tier is 'admin'
 * against its own UserStore, applies, then acks — which clears the row
 * whether applied or rejected, so a bad request can't retry forever).
 */
router.get('/stance/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT mode, requested_by, telegram_id FROM pending_stance WHERE id = 1');
    res.json({ pending: rows[0] || null });
  } catch (err) {
    console.error('Stance pending error:', err.message);
    res.status(500).json({ error: 'Failed to read pending stance' });
  }
});
router.post('/stance/ack', async (req, res) => {
  try {
    await pool.execute('DELETE FROM pending_stance WHERE id = 1');
    res.json({ ok: true });
  } catch (err) {
    console.error('Stance ack error:', err.message);
    res.status(500).json({ error: 'Failed to ack stance' });
  }
});

/**
 * POST /api/bot/sync/events
 * Bot pushes public agent mind-stream events (bot/core/agent_feed.py):
 * scan cycles, trade theses, opens/closes, trailing-stop moves, alerts,
 * stance changes. Stored in a bounded ring (agent_events) and re-broadcast
 * live to connected clients as SSE 'activity' events. (X-Bot-Secret authed.)
 *
 * Body: { events: [{ event_type, severity, symbol, title, body, data, ts }] }
 */
const FEED_TYPES = new Set(['scan', 'thesis', 'trade_open', 'trade_close',
  'sl_move', 'alert', 'stance', 'info']);
const FEED_SEVERITIES = new Set(['info', 'success', 'warning', 'critical']);
const FEED_KEEP = 500;           // ring size: newest N rows survive pruning
let feedInsertsSincePrune = 0;
router.post('/events', async (req, res) => {
  try {
    const events = Array.isArray(req.body?.events) ? req.body.events.slice(0, 50) : [];
    if (events.length === 0) {
      return res.status(400).json({ error: 'events array required' });
    }
    let inserted = 0;
    for (const ev of events) {
      const title = String(ev?.title || '').slice(0, 300);
      if (!title) continue;
      const type = FEED_TYPES.has(ev.event_type) ? ev.event_type : 'info';
      const severity = FEED_SEVERITIES.has(ev.severity) ? ev.severity : 'info';
      const symbol = String(ev.symbol || '').slice(0, 32);
      const body = String(ev.body || '').slice(0, 600);
      let dataJson = null;
      try {
        dataJson = ev.data && typeof ev.data === 'object'
          ? JSON.stringify(ev.data).slice(0, 2000) : null;
      } catch (e) { dataJson = null; }
      const ts = ev.ts ? new Date(ev.ts) : new Date();
      const at = isNaN(ts.getTime()) ? new Date() : ts;
      // Per-event fail-soft WITH the real driver error logged: one bad row
      // must not abort the batch, and a silent 500 to the bot's
      // fire-and-forget push left the feed empty with no trace of why.
      try {
        await pool.execute(
          `INSERT INTO agent_events (event_type, severity, symbol, title, body, data_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)`,
          [type, severity, symbol || null, title, body || null, dataJson, at]);
      } catch (insErr) {
        console.error('agent_events insert failed:', insErr.message,
          `(type=${type} at=${at.toISOString()})`);
        continue;
      }
      inserted++;
      nudge('activity', {
        event_type: type, severity, symbol, title, body,
        data: (ev.data && typeof ev.data === 'object') ? ev.data : {},
        created_at: at.toISOString(),
      });
      // Web push for the moments users actually want on their phone: trades
      // and alerts (scans/theses would be spam). Fire-and-forget — a push
      // service hiccup must never slow the bot's ingest.
      if (type === 'trade_open' || type === 'trade_close'
          || (type === 'alert' && severity !== 'info')) {
        try {
          const { notifySubscribers } = require('../lib/push');
          setImmediate(() => notifySubscribers({
            title: `RUNECLAW — ${title}`,
            body: body || 'Open the live feed for details.',
            url: '/dashboard#feed',
          }).catch(() => {}));
        } catch (e) { /* push is optional */ }
      }
    }
    // Ring-buffer prune, amortized (LIMIT/OFFSET inlined — placeholder
    // LIMITs break on some MySQL backends, see the markets-panel fix).
    feedInsertsSincePrune += inserted;
    if (feedInsertsSincePrune >= 50) {
      feedInsertsSincePrune = 0;
      try {
        const [old] = await pool.execute(
          `SELECT id FROM agent_events ORDER BY id DESC LIMIT 1 OFFSET ${FEED_KEEP}`);
        if (old.length > 0) {
          await pool.execute('DELETE FROM agent_events WHERE id <= ?', [old[0].id]);
        }
      } catch (pruneErr) { /* prune is best-effort */ }
    }
    res.json({ ok: true, inserted });
  } catch (err) {
    console.error('Agent feed sync error:', err.message);
    res.status(500).json({ error: 'Feed sync failed' });
  }
});

/**
 * POST /api/bot/sync/scan
 * Bot pushes GetClaw scan results after each scan cycle.
 * (authenticated — requires X-Bot-Secret)
 */
router.post('/scan', async (req, res) => {
  try {
    const incoming = req.body || {};
    // Preserve the deep-scan pattern block across scans that don't carry one.
    // A fresh block (from /deepscan) is stamped with its web arrival time; a
    // carried-forward block is dropped once older than the TTL.
    let deepscan = incoming.deepscan
      ? { ...incoming.deepscan, received_at: new Date().toISOString() }
      : (latestScan && latestScan.deepscan) || null;
    if (deepscan && deepscan.received_at) {
      const age = Date.now() - new Date(deepscan.received_at).getTime();
      if (!(age >= 0 && age < DEEPSCAN_TTL_MS)) deepscan = null;
    }
    latestScan = {
      ...incoming,
      ...(deepscan ? { deepscan } : {}),
      received_at: new Date().toISOString(),
    };
    // Persist to DB so it survives cold starts
    try {
      await pool.execute(
        'REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
        [JSON.stringify(latestScan)]
      );
    } catch (dbErr) {
      console.error('Scan cache write error:', dbErr.message);
    }
    // Update portfolio summary from circuit_breaker if present
    const cb = latestScan.circuit_breaker;
    if (cb && (cb.equity != null || cb.total_trades != null)) {
      latestPortfolio = {
        equity: cb.equity || 0,
        open_count: cb.open_count || 0,
        net_pnl: cb.net_pnl || 0,
        total_trades: cb.total_trades || 0,
        win_rate: cb.win_rate || 0,
        updated_at: latestScan.received_at,
      };
    }
    nudge('scan');
    res.json({ ok: true });
  } catch (err) {
    console.error('Scan sync error:', err.message);
    res.status(500).json({ error: 'Scan sync failed' });
  }
});

/**
 * POST /api/bot/sync/flight
 * Body: { records: [ joined DECISION↔OUTCOME flight records ],
 *         chain: { ok, length, tip_hash, problems } }
 *
 * Guardian Flight Recorder ingest. The bot pushes recent provenance-complete
 * decision records and the authoritative hash-chain verification result. Purely
 * a read-only mirror for the website — the tamper-evident ledger itself lives
 * bot-side. Bot-secret authed (botAuth middleware above).
 */
router.post('/flight', async (req, res) => {
  try {
    const body = req.body || {};
    const records = Array.isArray(body.records) ? body.records.slice(0, 200) : [];
    const chain = (body.chain && typeof body.chain === 'object') ? body.chain : {};
    const policy = (body.policy && typeof body.policy === 'object') ? body.policy : null;
    // Guardian console posture (chain health + per-module risk + armed flags).
    // Read-only, optional — older bots don't send it, so it stays null then.
    const guardian_status = (body.guardian_status && typeof body.guardian_status === 'object')
      ? body.guardian_status : null;
    latestFlight = { records, chain, policy, guardian_status, updated_at: new Date().toISOString() };
    // Safety incidents (blocks & recoveries) mirrored from the sealed chain.
    // Only attach when the bot actually sent the field — an OLDER bot omits it,
    // and leaving `incidents` absent lets the incidents route derive from
    // rejected records during the deploy transition (a sent [] means "synced,
    // genuinely none" and is left as-is).
    if (Array.isArray(body.incidents)) latestFlight.incidents = body.incidents.slice(0, 60);
    // Persist so it survives cold starts (table may not exist on older DBs —
    // in-memory still serves in that case).
    try {
      await pool.execute(
        'REPLACE INTO flight_cache (id, flight_json) VALUES (1, ?)',
        [JSON.stringify(latestFlight)]
      );
    } catch (dbErr) {
      console.error('Flight cache write error:', dbErr.message);
    }
    nudge('flight', { count: records.length, chain_ok: chain.ok !== false });
    res.json({ ok: true, stored: records.length });
  } catch (err) {
    console.error('Flight sync error:', err.message);
    res.status(500).json({ error: 'Flight sync failed' });
  }
});

/**
 * POST /api/bot/sync/signals
 * Body: { signals: [{ signal_key, symbol, direction, confidence, score, pattern,
 *         regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
 *         created_at, resolved_at }] }
 *
 * Append/UPSERT to the global signal stream. signal_key is the stable per-signal
 * id from the bot, so re-syncing the same signal updates its outcome (status/pnl)
 * rather than duplicating. Global stream (not per-user); the dashboard joins each
 * user's taken trades to it. Bot-secret authed (botAuth middleware above).
 */
router.post('/signals', async (req, res) => {
  try {
    const list = Array.isArray(req.body && req.body.signals) ? req.body.signals : [];
    if (list.length === 0) return res.json({ ok: true, upserted: 0 });
    // Cap a single batch to bound the write cost of a malformed/huge payload.
    const batch = list.slice(0, 500);
    let upserted = 0;
    for (const s of batch) {
      if (!s || !s.signal_key || !s.symbol || !s.direction) continue;
      await pool.execute(
        `INSERT INTO signals
           (signal_key, symbol, direction, confidence, score, pattern, regime,
            entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
            created_at, resolved_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON DUPLICATE KEY UPDATE
           status = VALUES(status), pnl = VALUES(pnl),
           resolved_at = VALUES(resolved_at)`,
        [
          String(s.signal_key).slice(0, 128),
          String(s.symbol).slice(0, 32),
          String(s.direction).slice(0, 8),
          Number(s.confidence) || 0,
          Number(s.score) || 0,
          s.pattern ? String(s.pattern).slice(0, 64) : null,
          s.regime ? String(s.regime).slice(0, 32) : null,
          Number(s.entry_price) || 0,
          Number(s.stop_loss) || 0,
          Number(s.take_profit) || 0,
          Number(s.rr) || 0,
          s.thesis != null ? String(s.thesis) : null,
          s.status ? String(s.status).slice(0, 16) : 'NEW',
          (s.pnl === null || s.pnl === undefined) ? null : Number(s.pnl),
          s.created_at ? new Date(s.created_at) : new Date(),
          s.resolved_at ? new Date(s.resolved_at) : null,
        ]
      );
      upserted++;
    }
    nudge('signals');
    res.json({ ok: true, upserted });
  } catch (err) {
    console.error('Signals sync error:', err.message);
    res.status(500).json({ error: 'Signals sync failed' });
  }
});

/**
 * GET /api/bot/sync/credentials/pending
 * Bot pulls pending exchange-credential requests (encrypted). The bot decrypts
 * (WEB_CREDS_KEY), imports into its Fernet store keyed by telegram_id (connect)
 * or removes them (disconnect), then ACKs so the row is cleared. Bot-secret authed.
 */
router.get('/credentials/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT user_id, telegram_id, exchange, action, encrypted_payload, created_at
       FROM pending_credentials ORDER BY created_at ASC LIMIT 100`
    );
    res.json({ pending: rows });
  } catch (err) {
    console.error('Cred pending fetch error:', err.message);
    res.status(500).json({ error: 'Failed to fetch pending credentials' });
  }
});

/**
 * POST /api/bot/sync/credentials/ack
 * Body: { acks: [{ user_id, action, ok }] }
 * For each successful ack, delete the pending row and update connection status
 * (connect -> connected=true, disconnect -> connected=false). Bot-secret authed.
 */
router.post('/credentials/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    for (const a of acks) {
      if (!a || a.user_id == null || !a.ok) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      // Carry the venue from the pending row (or the bot's ack) so the status
      // badge names the right exchange instead of always "bitget".
      const [prow] = await pool.execute(
        'SELECT exchange FROM pending_credentials WHERE user_id = ?', [uid]);
      const venue = String(a.venue || (prow[0] && prow[0].exchange) || 'bitget').toLowerCase();
      await pool.execute('DELETE FROM pending_credentials WHERE user_id = ?', [uid]);
      const connected = a.action === 'disconnect' ? false : true;
      await pool.execute(
        `INSERT INTO exchange_status (user_id, exchange, connected)
         VALUES (?, ?, ?)
         ON DUPLICATE KEY UPDATE exchange = VALUES(exchange),
           connected = VALUES(connected), updated_at = CURRENT_TIMESTAMP`,
        [uid, venue, connected]
      );
      applied++;
    }
    res.json({ ok: true, applied });
  } catch (err) {
    console.error('Cred ack error:', err.message);
    res.status(500).json({ error: 'Failed to ack credentials' });
  }
});

/**
 * GET /api/bot/sync/controls/pending
 * Bot pulls pending live-control changes (live on/off, margin cap, pause).
 * Bot-secret authed.
 */
router.get('/controls/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT user_id, telegram_id, live_enabled, max_margin, paused, created_at
       FROM pending_controls ORDER BY created_at ASC LIMIT 200`
    );
    res.json({ pending: rows });
  } catch (err) {
    console.error('Controls pending fetch error:', err.message);
    res.status(500).json({ error: 'Failed to fetch pending controls' });
  }
});

/**
 * POST /api/bot/sync/controls/ack
 * Body: { acks: [{ user_id, live_enabled, max_margin, paused, allowlisted, ok }] }
 * Bot reports the APPLIED state (from its UserStore). Clears the pending row and
 * mirrors the state into user_controls for the web UI. Bot-secret authed.
 */
router.post('/controls/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    for (const a of acks) {
      if (!a || a.user_id == null || !a.ok) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      await pool.execute('DELETE FROM pending_controls WHERE user_id = ?', [uid]);
      await pool.execute(
        `INSERT INTO user_controls (user_id, live_enabled, max_margin, paused, allowlisted)
         VALUES (?, ?, ?, ?, ?)
         ON DUPLICATE KEY UPDATE live_enabled = VALUES(live_enabled),
           max_margin = VALUES(max_margin), paused = VALUES(paused),
           allowlisted = VALUES(allowlisted), updated_at = CURRENT_TIMESTAMP`,
        [uid, a.live_enabled ? 1 : 0,
         (a.max_margin === null || a.max_margin === undefined) ? null : Number(a.max_margin),
         a.paused ? 1 : 0, a.allowlisted ? 1 : 0]
      );
      applied++;
    }
    res.json({ ok: true, applied });
  } catch (err) {
    console.error('Controls ack error:', err.message);
    res.status(500).json({ error: 'Failed to ack controls' });
  }
});

/**
 * GET /api/bot/sync/flatten/pending  — bot pulls emergency-stop flatten requests.
 * POST /api/bot/sync/flatten/ack { acks:[{user_id, ok}] } — clear completed ones.
 * Bot-secret authed. The bot closes the user's positions via THEIR own executor
 * before acking, so a failed close is retried next poll (row is left in place).
 */
router.get('/flatten/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT user_id, telegram_id, created_at FROM pending_flatten ORDER BY created_at ASC LIMIT 200');
    res.json({ pending: rows });
  } catch (err) {
    console.error('Flatten pending error:', err.message);
    res.status(500).json({ error: 'Failed to fetch flatten requests' });
  }
});

router.post('/flatten/ack', async (req, res) => {
  try {
    const acks = Array.isArray(req.body && req.body.acks) ? req.body.acks.slice(0, 200) : [];
    let applied = 0;
    for (const a of acks) {
      if (!a || a.user_id == null || !a.ok) continue;
      const uid = parseInt(a.user_id);
      if (!Number.isInteger(uid)) continue;
      await pool.execute('DELETE FROM pending_flatten WHERE user_id = ?', [uid]);
      applied++;
    }
    res.json({ ok: true, applied });
  } catch (err) {
    console.error('Flatten ack error:', err.message);
    res.status(500).json({ error: 'Failed to ack flatten' });
  }
});

/**
 * GET /api/bot/sync/leaderboard/pending
 * DESIRED-STATE pull (not a queue — no ack needed, idempotent): every user who
 * has opted in to the public leaderboard (anonymous handle set) AND has a
 * linked bot account. The bot publishes each user's own sealed, size-agnostic
 * statement under that handle and reconcile-removes handles that drop out of
 * this set, so opt-out (handle cleared) takes effect on the next pull.
 * Bot-secret authed.
 */
router.get('/leaderboard/pending', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT id AS user_id, telegram_id, leaderboard_handle AS handle
         FROM users
        WHERE leaderboard_handle IS NOT NULL AND telegram_id IS NOT NULL
        LIMIT 500`
    );
    res.json({ optins: rows });
  } catch (err) {
    console.error('Leaderboard pending fetch error:', err.message);
    res.status(500).json({ error: 'Failed to fetch leaderboard opt-ins' });
  }
});

/**
 * Telegram-parity reads (bot-secret authed): the bot renders the SAME
 * Node-side intelligence surfaces the web panels use — /exposure /research
 * /rwa on Telegram call these instead of duplicating the logic in Python.
 * All read-only; exposure maps the caller's telegram_id to their web account.
 */
router.get('/exposure', async (req, res) => {
  try {
    const tg = String(req.query.telegram_id || '').slice(0, 32);
    if (!tg) return res.status(400).json({ error: 'telegram_id required' });
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE telegram_id = ?', [tg]);
    if (!rows.length) return res.status(404).json({ error: 'No linked web account' });
    res.json(await require('../lib/exposure').buildExposure(rows[0].id));
  } catch (err) {
    console.error('Sync exposure error:', err.message);
    res.status(500).json({ error: 'Exposure unavailable' });
  }
});

router.get('/research/:symbol', async (req, res) => {
  try {
    const base = String(req.params.symbol || '').toUpperCase()
      .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
    if (!base) return res.status(400).json({ error: 'symbol required' });
    const d = await require('../lib/research').buildDossier(base);
    if (!d) return res.status(404).json({ error: 'Not listed on the venue — no trusted data' });
    res.json(d);
  } catch (err) {
    console.error('Sync research error:', err.message);
    res.status(500).json({ error: 'Research unavailable' });
  }
});

router.get('/rwa', async (req, res) => {
  try {
    res.json(await require('../lib/rwa').getRadar());
  } catch (err) {
    console.error('Sync rwa error:', err.message);
    res.status(500).json({ error: 'RWA radar unavailable' });
  }
});

// DEX taker-flow radar for the engine's gated on-chain voter (PR JJ) — the
// bot pulls the SAME payload the public Markets panel renders.
router.get('/onchain-flow', async (req, res) => {
  try {
    res.json(await require('../lib/onchain_flow').getFlowRadar());
  } catch (err) {
    console.error('Sync onchain-flow error:', err.message);
    res.status(500).json({ error: 'Flow radar unavailable' });
  }
});

// Read-side accessor for routes/guardian.js: in-memory first, DB on cold start.
async function getLatestFlight() {
  if (latestFlight) return latestFlight;
  try {
    const [rows] = await pool.execute(
      'SELECT flight_json FROM flight_cache WHERE id = 1');
    if (rows.length > 0 && rows[0].flight_json) {
      latestFlight = JSON.parse(rows[0].flight_json);
    }
  } catch (err) { /* cold-start miss / table absent is fine */ }
  return latestFlight;
}

module.exports = router;
// Named accessor for routes/reports.js (in-memory + DB cold-start fallback).
module.exports.getLatestReports = getLatestReports;
// Named accessor for routes/macro.js — the synced scan's BTC regime block.
module.exports.getLatestScan = () => latestScan;
// Named accessor for routes/guardian.js — the Flight Recorder ledger mirror.
module.exports.getLatestFlight = getLatestFlight;
