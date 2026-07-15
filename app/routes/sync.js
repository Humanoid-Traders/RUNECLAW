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

    // Clear existing trades and snapshots for this user
    await pool.execute('DELETE FROM trades WHERE user_id = ?', [user_id]);
    await pool.execute('DELETE FROM equity_snapshots WHERE user_id = ?', [user_id]);

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

    // Insert equity snapshot
    const eq = equity || 0;
    await pool.execute(
      'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
      [user_id, eq, new Date()]
    );

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

    // Record equity snapshot
    if (equity !== undefined) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [user_id, equity, new Date()]
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
 * POST /api/bot/sync/scan
 * Bot pushes GetClaw scan results after each scan cycle.
 * (authenticated — requires X-Bot-Secret)
 */
router.post('/scan', async (req, res) => {
  try {
    latestScan = {
      ...req.body,
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
      await pool.execute('DELETE FROM pending_credentials WHERE user_id = ?', [uid]);
      const connected = a.action === 'disconnect' ? false : true;
      await pool.execute(
        `INSERT INTO exchange_status (user_id, exchange, connected)
         VALUES (?, 'bitget', ?)
         ON DUPLICATE KEY UPDATE connected = VALUES(connected),
           updated_at = CURRENT_TIMESTAMP`,
        [uid, connected]
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

module.exports = router;
