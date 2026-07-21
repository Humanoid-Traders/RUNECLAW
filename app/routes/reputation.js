/**
 * Outcome-Based Agent Reputation — web surface.
 *
 * Serves a verifiable, confidence-adjusted reputation score computed only from
 * the user's OWN realized closed trades. Self-contained on the DB layer.
 * ADVISORY ONLY — a heuristic score, never a verdict (§4). Dollar-free (all
 * ratios), so it reads honestly without inventing a starting balance.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { computeReputation } = require('../lib/reputation');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

// GET /api/reputation — the user's outcome-based reputation readout.
router.get('/', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, size_usd, fees, opened_at, closed_at
         FROM trades WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
         ORDER BY closed_at ASC`,
      [uid]
    );
    // Seed the drawdown-% denominator from the latest equity snapshot when we
    // have one (matches /api/trades/breakdown), else a neutral default.
    const [snap] = await pool.execute(
      'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [uid]
    );
    const net = rows.reduce((a, r) => a + (parseFloat(r.pnl) || 0), 0);
    const startEquity = snap.length > 0 ? Math.max(parseFloat(snap[0].equity) - net, 1) : 10000;
    res.json(computeReputation(rows, { startEquity }));
  } catch (err) {
    console.error('Reputation error:', err.message);
    res.status(500).json({ error: 'Failed to compute reputation' });
  }
});

module.exports = router;
