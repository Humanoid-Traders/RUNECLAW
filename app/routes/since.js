'use strict';
/**
 * "While you were away" — GET /api/since. The dashboard's welcome-back
 * digest: how long you were gone and what ACTUALLY happened in between —
 * new engine signals, engine events, and your own paper closes. Every number
 * is a real count over the caller's absence window; a first visit returns
 * { first_visit: true } and never a back-filled history (honesty doctrine).
 *
 * Private per-user surface: virtual vUSDT pnl for the caller's own arena
 * closes is allowed here (§4 keeps dollars off PUBLIC surfaces only).
 * Reading the digest advances last_seen_at — the window always starts where
 * the previous one ended.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { pool } = require('../db');

const router = express.Router();

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

router.get('/', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const [rows] = await pool.execute(
      'SELECT id, last_seen_at FROM users WHERE id = ?', [userId]);
    if (!rows[0]) return res.status(404).json({ error: 'User not found' });
    const last = rows[0].last_seen_at ? new Date(rows[0].last_seen_at) : null;
    const now = new Date();
    await pool.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', [now, userId]);
    if (!last || Number.isNaN(last.getTime())) {
      return res.json({ first_visit: true });
    }
    const out = {
      away_s: Math.max(0, Math.floor((now.getTime() - last.getTime()) / 1000)),
      since: last.toISOString(),
      signals_new: 0,
      events_new: 0,
      arena: { closes: 0, pnl: 0 },
    };
    try {
      const [sc] = await pool.execute(
        'SELECT COUNT(*) AS n FROM signals WHERE created_at >= ?', [last]);
      out.signals_new = Number(sc[0] && sc[0].n) || 0;
    } catch (e) { /* stream quiet → 0 */ }
    try {
      const [ec] = await pool.execute(
        'SELECT COUNT(*) AS n FROM agent_events WHERE created_at >= ?', [last]);
      out.events_new = Number(ec[0] && ec[0].n) || 0;
    } catch (e) { /* mind stream quiet → 0 */ }
    try {
      const [tr] = await pool.execute(
        'SELECT pnl FROM arena_trades WHERE user_id = ? AND closed_at >= ?',
        [userId, last]);
      out.arena.closes = tr.length;
      out.arena.pnl = round2(tr.reduce((a, t) => a + (Number(t.pnl) || 0), 0));
    } catch (e) { /* no arena activity → 0 */ }
    res.json(out);
  } catch (err) {
    console.error('Since error:', err.message);
    res.status(500).json({ error: 'Digest unavailable' });
  }
});

module.exports = router;
