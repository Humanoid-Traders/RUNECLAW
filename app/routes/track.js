/**
 * Public track record — the operator engine's verifiable performance.
 *
 * GET /api/public/track-record (no auth). Everything is aggregated from data
 * the bot already syncs: closed trades + equity snapshots of the operator
 * account (user 1) and the scan cache's live_mode flag. Nothing here is
 * hand-entered — if the bot didn't trade it, it isn't on the page. Numbers
 * the data can't support (no snapshots yet, no closes yet) are returned as
 * null and the page says "no data", never a made-up figure.
 *
 * Cached in-memory for 5 minutes: the page is public and this keeps a
 * traffic spike from hammering MySQL.
 */

const express = require('express');
const { pool } = require('../db');

const router = express.Router();

const OPERATOR_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;
const CACHE_MS = 5 * 60 * 1000;
let cache = null;          // { at: ms, payload }

function round2(v) { return Math.round(v * 100) / 100; }

// Largest peak-to-trough drop over an equity series, as a % of the peak.
function maxDrawdownPct(curve) {
  let peak = -Infinity, maxDd = 0;
  for (const p of curve) {
    peak = Math.max(peak, p.equity);
    if (peak > 0) maxDd = Math.max(maxDd, (peak - p.equity) / peak * 100);
  }
  return round2(maxDd);
}

// Downsample to at most n points, always keeping the first and last.
function downsample(rows, n) {
  if (rows.length <= n) return rows;
  const step = (rows.length - 1) / (n - 1);
  const out = [];
  for (let i = 0; i < n; i++) out.push(rows[Math.round(i * step)]);
  return out;
}

router.get('/track-record', async (req, res) => {
  try {
    if (cache && Date.now() - cache.at < CACHE_MS) {
      res.setHeader('Cache-Control', 'public, max-age=120');
      return res.json(cache.payload);
    }

    const [trades] = await pool.execute(
      `SELECT symbol, direction, pnl, fees, opened_at, closed_at
         FROM trades
        WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
    const [snaps] = await pool.execute(
      `SELECT equity, snapshot_at FROM equity_snapshots
        WHERE user_id = ? ORDER BY snapshot_at ASC`, [OPERATOR_USER_ID]);

    // Current mode from the bot's scan cache — shown as a badge, and honest:
    // absent cache means "unknown", not a guess.
    let mode = null;
    try {
      const [rows] = await pool.execute('SELECT scan_json FROM scan_cache WHERE id = 1');
      if (rows.length && rows[0].scan_json) {
        const cb = (JSON.parse(rows[0].scan_json) || {}).circuit_breaker || {};
        if (typeof cb.live_mode === 'boolean') mode = cb.live_mode ? 'LIVE' : 'PAPER';
      }
    } catch (e) { /* badge stays unknown */ }

    const pnls = trades.map(t => parseFloat(t.pnl) || 0);
    const wins = pnls.filter(p => p > 0);
    const losses = pnls.filter(p => p < 0);
    const grossWin = wins.reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(losses.reduce((a, b) => a + b, 0));
    const netPnl = round2(pnls.reduce((a, b) => a + b, 0));
    const fees = round2(trades.reduce((a, b) => a + (parseFloat(b.fees) || 0), 0));

    // Monthly net PnL from closed trades (calendar months, UTC).
    const monthly = {};
    for (const t of trades) {
      const d = new Date(t.closed_at);
      const key = `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
      monthly[key] = round2((monthly[key] || 0) + (parseFloat(t.pnl) || 0));
    }

    const curve = snaps.map(s => ({
      t: new Date(s.snapshot_at).getTime(),
      equity: parseFloat(s.equity),
    })).filter(p => isFinite(p.equity) && p.equity > 0);

    const payload = {
      generated_at: new Date().toISOString(),
      mode,                                       // 'LIVE' | 'PAPER' | null
      venue: 'Bitget USDT-M perpetuals',
      stats: {
        trades: trades.length,
        wins: wins.length,
        losses: losses.length,
        win_rate_pct: trades.length ? round2(wins.length / trades.length * 100) : null,
        net_pnl_usd: trades.length ? netPnl : null,
        fees_usd: trades.length ? fees : null,
        profit_factor: grossLoss > 0 ? round2(grossWin / grossLoss) : null,
        avg_win_usd: wins.length ? round2(grossWin / wins.length) : null,
        avg_loss_usd: losses.length ? round2(-grossLoss / losses.length) : null,
        max_drawdown_pct: curve.length >= 2 ? maxDrawdownPct(curve) : null,
        current_equity_usd: curve.length ? round2(curve[curve.length - 1].equity) : null,
        first_trade_at: trades.length ? trades[0].closed_at : null,
        last_trade_at: trades.length ? trades[trades.length - 1].closed_at : null,
      },
      monthly_pnl_usd: monthly,
      equity_curve: downsample(curve, 400),
      recent_trades: trades.slice(-20).reverse().map(t => ({
        symbol: t.symbol, direction: t.direction,
        pnl: round2(parseFloat(t.pnl) || 0), closed_at: t.closed_at,
      })),
    };
    cache = { at: Date.now(), payload };
    res.setHeader('Cache-Control', 'public, max-age=120');
    res.json(payload);
  } catch (err) {
    console.error('Track record error:', err.message);
    res.status(500).json({ error: 'Track record unavailable' });
  }
});

module.exports = router;
