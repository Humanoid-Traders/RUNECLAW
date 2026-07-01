const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { computePerformance } = require('../lib/trade_performance');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();

// All routes require auth
router.use(authMiddleware);

const notesLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

// GET /api/trades/stats - Portfolio statistics
router.get('/stats', async (req, res) => {
  try {
    const uid = req.user.user_id;

    const [pnlRows] = await pool.execute(
      'SELECT COALESCE(SUM(pnl), 0) as net_pnl, COALESCE(SUM(fees), 0) as total_fees, COUNT(*) as total_trades FROM trades WHERE user_id = ? AND status = ?',
      [uid, 'CLOSED']
    );

    const [winRows] = await pool.execute(
      'SELECT COUNT(*) as wins FROM trades WHERE user_id = ? AND status = ? AND pnl > 0',
      [uid, 'CLOSED']
    );

    const [allPnl] = await pool.execute(
      'SELECT pnl, size_usd FROM trades WHERE user_id = ? AND status = ? ORDER BY closed_at',
      [uid, 'CLOSED']
    );

    const [openRows] = await pool.execute(
      'SELECT COUNT(*) as open_count FROM trades WHERE user_id = ? AND status = ?',
      [uid, 'OPEN']
    );

    const netPnl = parseFloat(pnlRows[0].net_pnl);
    const totalFees = parseFloat(pnlRows[0].total_fees);
    const totalTrades = parseInt(pnlRows[0].total_trades);
    const wins = parseInt(winRows[0].wins);
    const winRate = totalTrades > 0 ? (wins / totalTrades * 100) : 0;

    // Use latest synced equity snapshot if available
    const [snapRows] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    const equity = snapRows.length > 0 ? parseFloat(snapRows[0].equity) : (10000 + netPnl);

    // Compute Sharpe from trade returns
    let sharpe = 0;
    if (allPnl.length >= 2) {
      const returns = allPnl.map(r => parseFloat(r.pnl) / parseFloat(r.size_usd));
      const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
      const variance = returns.reduce((a, b) => a + (b - mean) ** 2, 0) / (returns.length - 1);
      const std = Math.sqrt(variance);
      if (std > 0) sharpe = (mean / std) * Math.sqrt(252);
    }

    // Profit factor
    const grossWins = allPnl.filter(r => parseFloat(r.pnl) > 0).reduce((a, r) => a + parseFloat(r.pnl), 0);
    const grossLosses = Math.abs(allPnl.filter(r => parseFloat(r.pnl) < 0).reduce((a, r) => a + parseFloat(r.pnl), 0));
    const profitFactor = grossLosses > 0 ? grossWins / grossLosses : grossWins > 0 ? 999 : 0;

    res.json({
      equity: Math.round(equity * 100) / 100,
      net_pnl: Math.round(netPnl * 100) / 100,
      total_fees: Math.round(totalFees * 100) / 100,
      total_trades: totalTrades,
      open_positions: parseInt(openRows[0].open_count),
      win_rate: Math.round(winRate * 10) / 10,
      sharpe: Math.round(sharpe * 100) / 100,
      profit_factor: Math.round(profitFactor * 100) / 100,
      wins,
      losses: totalTrades - wins,
    });
  } catch (err) {
    console.error('Stats error:', err.message);
    res.status(500).json({ error: 'Failed to compute stats' });
  }
});

// GET /api/trades/history - Closed trades
router.get('/history', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const offset = parseInt(req.query.offset) || 0;

    const [rows] = await pool.execute(
      `SELECT id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at, notes
       FROM trades WHERE user_id = ? AND status = 'CLOSED'
       ORDER BY closed_at DESC LIMIT ? OFFSET ?`,
      [uid, limit, offset]
    );

    const [countRows] = await pool.execute(
      "SELECT COUNT(*) as total FROM trades WHERE user_id = ? AND status = 'CLOSED'",
      [uid]
    );

    res.json({ trades: rows, total: parseInt(countRows[0].total) });
  } catch (err) {
    console.error('History error:', err.message);
    res.status(500).json({ error: 'Failed to fetch history' });
  }
});

// GET /api/trades/open - Open positions
router.get('/open', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT id, symbol, direction, entry_price, size_usd, fees, pattern, stop_loss, take_profit, opened_at
       FROM trades WHERE user_id = ? AND status = 'OPEN'
       ORDER BY opened_at DESC`,
      [uid]
    );
    res.json({ positions: rows });
  } catch (err) {
    console.error('Open error:', err.message);
    res.status(500).json({ error: 'Failed to fetch positions' });
  }
});

// PATCH /api/trades/:id/notes - attach a journal note to one of the user's
// OWN closed trades. Turns the trade history from a ledger into something
// worth reviewing (why a setup worked or didn't).
router.patch('/:id/notes', notesLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const id = parseInt(req.params.id);
    if (!Number.isInteger(id)) return res.status(400).json({ error: 'Invalid trade id' });
    const notes = typeof req.body.notes === 'string' ? req.body.notes.slice(0, 2000) : '';
    const [result] = await pool.execute(
      'UPDATE trades SET notes = ? WHERE id = ? AND user_id = ?',
      [notes, id, uid]
    );
    if (!result.affectedRows) return res.status(404).json({ error: 'Trade not found' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Trade notes error:', err.message);
    res.status(500).json({ error: 'Failed to save note' });
  }
});

// GET /api/trades/breakdown - realised PnL by symbol + max drawdown + expectancy.
// Computed in-process over the user's closed trades (bounded window) so it
// behaves the same on MySQL and the in-memory mock.
router.get('/breakdown', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, closed_at
       FROM trades WHERE user_id = ? AND status = 'CLOSED'
       ORDER BY closed_at DESC LIMIT 2000`,
      [uid]
    );
    // Seed drawdown % against the latest equity snapshot if we have one.
    const [snap] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    const net = rows.reduce((a, r) => a + (parseFloat(r.pnl) || 0), 0);
    const startEquity = snap.length > 0 ? Math.max(parseFloat(snap[0].equity) - net, 1) : 10000;
    res.json(computePerformance(rows, { startEquity }));
  } catch (err) {
    console.error('Breakdown error:', err.message);
    res.status(500).json({ error: 'Failed to compute breakdown' });
  }
});

// GET /api/trades/equity-curve - Equity snapshots
router.get('/equity-curve', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at ASC LIMIT 365',
      [uid]
    );
    res.json({ snapshots: rows });
  } catch (err) {
    console.error('Equity curve error:', err.message);
    res.status(500).json({ error: 'Failed to fetch equity curve' });
  }
});

module.exports = router;
