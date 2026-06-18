/**
 * Bot -> Website data sync endpoint.
 * The Telegram bot calls this to push real portfolio & trade data.
 * Authenticated via a shared secret (BOT_SYNC_SECRET).
 */

const express = require('express');
const { pool } = require('../db');

const router = express.Router();
const SYNC_SECRET = process.env.BOT_SYNC_SECRET || 'runeclaw-sync-2026';

// Auth middleware for bot sync
function botAuth(req, res, next) {
  const secret = req.headers['x-bot-secret'];
  if (!secret || secret !== SYNC_SECRET) {
    return res.status(403).json({ error: 'Invalid bot secret' });
  }
  next();
}

router.use(botAuth);

// GET /api/bot/sync/users - List all users (for debugging)
router.get('/users', async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, email, plan, telegram_linked FROM users');
    res.json({ users: rows });
  } catch (err) {
    console.error('Users list error:', err.message);
    res.status(500).json({ error: 'Failed to list users' });
  }
});

/**
 * POST /api/bot/sync
 * Body: {
 *   user_id: number,
 *   equity: number,
 *   positions: [{ symbol, direction, entry_price, size_usd, fees, pattern, stop_loss, take_profit, opened_at }],
 *   closed_trades: [{ symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at }]
 * }
 *
 * Replaces all trade data for the user with the synced data.
 */
router.post('/', async (req, res) => {
  try {
    const { user_id, equity, positions, closed_trades } = req.body;
    if (!user_id) return res.status(400).json({ error: 'user_id required' });

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

    res.json({ ok: true, synced: { closed: (closed_trades || []).length, open: (positions || []).length, equity: eq } });
  } catch (err) {
    console.error('Sync error:', err.message);
    res.status(500).json({ error: 'Sync failed' });
  }
});

/**
 * POST /api/bot/trade-event
 * Called by the bot when a single trade opens or closes.
 * Body: { user_id, event: "open"|"close", trade: {...}, equity }
 */
router.post('/trade-event', async (req, res) => {
  try {
    const { user_id, event, trade, equity } = req.body;
    if (!user_id || !event || !trade) {
      return res.status(400).json({ error: 'user_id, event, and trade required' });
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

module.exports = router;
