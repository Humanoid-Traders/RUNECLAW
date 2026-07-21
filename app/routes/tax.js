/**
 * Continuous Tax & Compliance Agent — web surface.
 *
 * Turns a user's own closed-trade history into a realized-gains report:
 * per-disposal rows, per-year summaries (short/long-term split), and a
 * Form-8949-friendly CSV export. Self-contained on the DB layer — no engine
 * round-trip — so it works whenever the site is up. INFORMATIONAL ONLY; every
 * response carries the "not tax advice" disclaimer (§4: heuristic flags, never
 * verdicts).
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { buildReport, toCsv } = require('../lib/tax');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

// Read this user's own closed trades, oldest-first (natural for a tax ledger).
async function loadClosed(uid) {
  const [rows] = await pool.execute(
    `SELECT symbol, direction, entry_price, exit_price, size_usd, pnl, fees, opened_at, closed_at
       FROM trades WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
       ORDER BY closed_at ASC`,
    [uid]
  );
  return rows;
}

function parseYear(q) {
  if (q == null || q === '') return null;
  const y = parseInt(q, 10);
  return Number.isInteger(y) && y >= 2000 && y <= 2100 ? y : null;
}

// GET /api/tax/report?year=YYYY — realized-gains report (all years if no year).
router.get('/report', async (req, res) => {
  try {
    const rows = await loadClosed(req.user.user_id);
    res.json(buildReport(rows, { year: parseYear(req.query.year) }));
  } catch (err) {
    console.error('Tax report error:', err.message);
    res.status(500).json({ error: 'Failed to build tax report' });
  }
});

// GET /api/tax/export.csv?year=YYYY — Form-8949-friendly CSV download.
router.get('/export.csv', async (req, res) => {
  try {
    const year = parseYear(req.query.year);
    const rows = await loadClosed(req.user.user_id);
    const report = buildReport(rows, { year });
    const fname = `runeclaw-tax-${year != null ? year : 'all'}.csv`;
    res.setHeader('Content-Type', 'text/csv; charset=utf-8');
    res.setHeader('Content-Disposition', `attachment; filename="${fname}"`);
    res.send(toCsv(report.disposals));
  } catch (err) {
    console.error('Tax export error:', err.message);
    res.status(500).json({ error: 'Failed to export tax CSV' });
  }
});

module.exports = router;
