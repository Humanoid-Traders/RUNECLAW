/**
 * Bot -> Website data sync endpoint.
 * The Telegram bot calls this to push real portfolio & trade data.
 * Authenticated via a shared secret (BOT_SYNC_SECRET).
 */

const express = require('express');
const crypto = require('crypto');
const { pool } = require('../db');

const router = express.Router();

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
router.get('/scan', (req, res) => {
  if (!latestScan) {
    return res.json({ scan: null, message: 'No scan data yet. Run /scan in Telegram.' });
  }
  res.json({ scan: latestScan });
});

/**
 * GET /api/bot/sync/portfolio-summary
 * Dashboard fetches bot portfolio summary (no auth required — shows synced data).
 */
router.get('/portfolio-summary', async (req, res) => {
  // Return cached in-memory summary if available
  if (latestPortfolio) {
    return res.json({ portfolio: latestPortfolio });
  }
  // Fallback: try to read from DB (latest equity snapshot for any user)
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
    const equity = snapRows.length > 0 ? parseFloat(snapRows[0].equity) : 800;
    const total = tradeRows[0]?.total || 0;
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
    res.json({ portfolio: { equity: 800, open_count: 0, net_pnl: 0, total_trades: 0, win_rate: 0 } });
  }
});

// Auth middleware for bot sync — constant-time comparison
function botAuth(req, res, next) {
  if (!SYNC_SECRET) {
    return res.status(503).json({ error: 'Sync not configured (BOT_SYNC_SECRET unset)' });
  }
  const secret = req.headers['x-bot-secret'];
  if (!secret || !crypto.timingSafeEqual(Buffer.from(secret), Buffer.from(SYNC_SECRET))) {
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
    res.json({ ok: true });
  } catch (err) {
    console.error('Scan sync error:', err.message);
    res.status(500).json({ error: 'Scan sync failed' });
  }
});

module.exports = router;
