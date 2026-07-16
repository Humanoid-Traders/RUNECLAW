/**
 * Signal stream (read).
 * Serves the global signal stream the bot pushes via /api/bot/sync/signals.
 * Public market data (like /api/bot/sync/scan) — no auth required.
 */

const express = require('express');
const { pool } = require('../db');
const { computeAnalytics } = require('../lib/signal_analytics');

const router = express.Router();

// GET /api/signals?limit=&status=&symbol=&direction=&min_confidence=
// Recent global signals, newest first. Optional filters.
router.get('/', async (req, res) => {
  try {
    const limit = Math.min(parseInt(req.query.limit) || 50, 200);
    const where = [];
    const params = [];
    if (req.query.status) { where.push('status = ?'); params.push(String(req.query.status).slice(0, 16)); }
    if (req.query.symbol) { where.push('symbol = ?'); params.push(String(req.query.symbol).slice(0, 32)); }
    if (req.query.direction) { where.push('direction = ?'); params.push(String(req.query.direction).slice(0, 8).toUpperCase()); }
    if (req.query.min_confidence) {
      const mc = Number(req.query.min_confidence);
      if (Number.isFinite(mc)) { where.push('confidence >= ?'); params.push(mc); }
    }
    const clause = where.length ? `WHERE ${where.join(' AND ')}` : '';
    const [rows] = await pool.execute(
      `SELECT signal_key, symbol, direction, confidence, score, pattern, regime,
              entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
              created_at, resolved_at
       FROM signals ${clause}
       ORDER BY created_at DESC LIMIT ${limit}`,
      params
    );
    res.json({ signals: rows });
  } catch (err) {
    console.error('Signals fetch error:', err.message);
    // Fail soft — an empty stream is better than a dashboard error.
    res.json({ signals: [] });
  }
});

// GET /api/signals/stats - aggregate signal performance (resolved signals only).
router.get('/stats', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT COUNT(*) AS resolved,
              SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
              COALESCE(SUM(pnl), 0) AS net_pnl
       FROM signals WHERE pnl IS NOT NULL`
    );
    const r = rows[0] || {};
    const resolved = parseInt(r.resolved || 0);
    const wins = parseInt(r.wins || 0);
    res.json({
      resolved,
      wins,
      losses: resolved - wins,
      win_rate: resolved > 0 ? Math.round((wins / resolved) * 1000) / 10 : 0,
      net_pnl: Math.round(parseFloat(r.net_pnl || 0) * 100) / 100,
    });
  } catch (err) {
    console.error('Signal stats error:', err.message);
    res.json({ resolved: 0, wins: 0, losses: 0, win_rate: 0, net_pnl: 0 });
  }
});

// GET /api/signals/analytics - win-rate / net-pnl broken down by pattern,
// symbol, direction and confidence bucket (resolved signals only). Aggregation
// runs in-process over a bounded window so it behaves the same on MySQL and the
// in-memory mock (which ignores WHERE clauses).
const EMPTY_ANALYTICS = {
  overall: { resolved: 0, wins: 0, losses: 0, win_rate: 0, net_pnl: 0 },
  by_pattern: [], by_symbol: [], by_direction: [], by_confidence: [],
};
router.get('/analytics', async (req, res) => {
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, confidence, pattern, pnl
       FROM signals WHERE pnl IS NOT NULL
       ORDER BY resolved_at DESC LIMIT 2000`
    );
    res.json(computeAnalytics(rows));
  } catch (err) {
    console.error('Signal analytics error:', err.message);
    res.json(EMPTY_ANALYTICS); // fail soft — empty insights beat a dashboard error
  }
});

module.exports = router;
