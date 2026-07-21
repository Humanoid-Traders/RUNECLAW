/**
 * Leaderboard — opt-in, anonymous handles, ranked by return %.
 *
 * Privacy: appearing is OFF by default. A user opts in by choosing a display
 * handle (never their email). The board shows the handle, a return %, a trade
 * count, and win rate — NEVER a dollar amount, so account size never leaks.
 * Return % is computed on the standard paper stake so ranks are comparable
 * across accounts and reveal no balance. It reads only realized PnL and does
 * not touch the money path or the live-eligibility gate.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { pool } = require('../db');

const router = express.Router();
router.use(authMiddleware);

const PAPER_BASE = 10000;                 // the standard paper starting stake
const HANDLE_RE = /^[A-Za-z0-9_]{3,20}$/;
const MAX_ROWS = 50;
const optLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

// Per-user realized stats from CLOSED trades — same queries the Portfolio
// stats panel uses, so this stays consistent with what the user sees.
async function userStats(uid) {
  const [agg] = await pool.execute(
    'SELECT COALESCE(SUM(pnl), 0) as net_pnl, COALESCE(SUM(fees), 0) as total_fees, COUNT(*) as total_trades FROM trades WHERE user_id = ? AND status = ?',
    [uid, 'CLOSED']);
  const [w] = await pool.execute(
    'SELECT COUNT(*) as wins FROM trades WHERE user_id = ? AND status = ? AND pnl > 0',
    [uid, 'CLOSED']);
  const net = parseFloat(agg[0] && agg[0].net_pnl) || 0;
  const trades = Number(agg[0] && agg[0].total_trades) || 0;
  const wins = Number(w[0] && w[0].wins) || 0;
  return {
    return_pct: Math.round((net / PAPER_BASE) * 10000) / 100,   // % to 2dp
    trades,
    win_rate: trades ? Math.round((wins / trades) * 1000) / 10 : 0,
  };
}

// GET /  → ranked board (opted-in members with >=1 closed trade) + caller state.
router.get('/', async (req, res) => {
  try {
    const [members] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const scored = [];
    for (const m of members) {
      const s = await userStats(m.id);
      if (s.trades > 0) scored.push({ id: m.id, handle: m.leaderboard_handle, ...s });
    }
    scored.sort((a, b) => b.return_pct - a.return_pct);
    const rows = scored.slice(0, MAX_ROWS).map((r, i) => ({
      rank: i + 1, handle: r.handle, return_pct: r.return_pct,
      trades: r.trades, win_rate: r.win_rate, is_me: r.id === req.user.user_id,
    }));
    const [me] = await pool.execute('SELECT leaderboard_handle FROM users WHERE id = ?', [req.user.user_id]);
    const handle = (me[0] && me[0].leaderboard_handle) || null;
    // UX-6: the caller's REAL rank — even when they're outside the top MAX_ROWS
    // window (the board itself is capped, so >50th place used to be invisible
    // and unmotivating). Rank + total are position-only, no dollar figures.
    const myIdx = scored.findIndex((r) => r.id === req.user.user_id);
    const my_rank = myIdx >= 0 ? myIdx + 1 : null;
    res.json({ rows, opted_in: !!handle, handle, my_rank, ranked_total: scored.length });
  } catch (err) {
    console.error('Leaderboard error:', err.message);
    res.status(500).json({ error: 'Failed to load leaderboard' });
  }
});

// POST /opt-in { handle } — join (or rename) with an anonymous handle.
router.post('/opt-in', optLimit, async (req, res) => {
  try {
    const handle = String((req.body || {}).handle || '').trim();
    if (!HANDLE_RE.test(handle)) {
      return res.status(400).json({ error: 'Handle must be 3–20 letters, numbers, or underscores.' });
    }
    // Case-insensitive uniqueness, ignoring the caller's own current handle.
    const [taken] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
    if (taken.length && taken[0].id !== req.user.user_id) {
      return res.status(409).json({ error: 'That handle is taken — try another.' });
    }
    await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', [handle, req.user.user_id]);
    res.json({ ok: true, handle });
  } catch (err) {
    console.error('Leaderboard opt-in error:', err.message);
    res.status(500).json({ error: 'Could not join the leaderboard' });
  }
});

// POST /opt-out — leave the board (clears the handle).
router.post('/opt-out', optLimit, async (req, res) => {
  try {
    await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', [null, req.user.user_id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Leaderboard opt-out error:', err.message);
    res.status(500).json({ error: 'Could not leave the leaderboard' });
  }
});

module.exports = router;
