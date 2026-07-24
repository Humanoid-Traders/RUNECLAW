'use strict';
/**
 * User watchlist — starred symbols. GET returns the caller's list; POST
 * /toggle stars or unstars one symbol (normalized + validated, capped so a
 * runaway client can't hoard). Private per-user data; the list feeds the
 * dashboard watchlist strip and extends the pattern watch (engine pattern
 * pushes for WATCHED symbols, not just held ones).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');

const router = express.Router();

const SYM_RE = /^[A-Z0-9]{2,20}$/;
const MAX_WATCH = 30;

// "sol" / "Sol/usdt" → "SOLUSDT" — the same normalization the Arena ticket
// applies, so a symbol star matches what charts and the engine call it.
function normSym(v) {
  v = String(v == null ? '' : v).trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (v && !/USDT$/.test(v)) v += 'USDT';
  return v;
}

router.get('/', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT symbol FROM user_watchlist WHERE user_id = ?', [req.user.user_id]);
    res.json({ symbols: rows.map((r) => r.symbol), max: MAX_WATCH });
  } catch (err) {
    console.error('Watchlist error:', err.message);
    res.status(500).json({ error: 'Watchlist unavailable' });
  }
});

router.post('/toggle', authMiddleware, async (req, res) => {
  try {
    const sym = normSym((req.body || {}).symbol);
    if (!SYM_RE.test(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const userId = req.user.user_id;
    const [rows] = await pool.execute(
      'SELECT symbol FROM user_watchlist WHERE user_id = ?', [userId]);
    if (rows.some((r) => r.symbol === sym)) {
      await pool.execute(
        'DELETE FROM user_watchlist WHERE user_id = ? AND symbol = ?', [userId, sym]);
      return res.json({ ok: true, watching: false, symbol: sym });
    }
    if (rows.length >= MAX_WATCH) {
      return res.status(400).json({ error: `Watchlist is full (${MAX_WATCH} symbols) — unstar something first` });
    }
    await pool.execute(
      'INSERT INTO user_watchlist (user_id, symbol, created_at) VALUES (?, ?, ?)',
      [userId, sym, new Date()]);
    res.json({ ok: true, watching: true, symbol: sym });
  } catch (err) {
    console.error('Watchlist toggle error:', err.message);
    res.status(500).json({ error: 'Watchlist update failed' });
  }
});

module.exports = router;
module.exports.MAX_WATCH = MAX_WATCH;
