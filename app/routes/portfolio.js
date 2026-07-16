/**
 * Per-user portfolio truth (JWT-authed).
 *
 * GET /api/portfolio — fetches the caller's OWN paper portfolio from the bot
 * gateway (engine.user_portfolios) and write-throughs it into the website DB
 * under the JWT user_id, so every existing /api/trades/* endpoint (stats,
 * history, equity-curve, breakdown, activity, journal notes) becomes
 * per-user-correct without query changes.
 *
 * Why: routes/sync.js server-forces all bot pushes to the operator account
 * (user 1); a web user's trades otherwise never reach the DB.
 *
 * Write-through rules:
 *  - equity snapshot inserted only when equity changed or the latest snapshot
 *    is older than 15 minutes (keeps the curve meaningful, bounds growth);
 *  - CLOSED trades are UPSERTED by (symbol, closed_at, pnl) — only missing
 *    rows are inserted, so journal notes on existing rows survive (unlike
 *    sync.js's delete-all pattern). The key can theoretically collide for two
 *    identical same-second closes — acceptable for paper v1.
 *  - OPEN rows are replaced wholesale (they carry no user annotations).
 *
 * On gateway failure the route degrades to DB-only data with stale: true.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

const pfLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

const SNAPSHOT_MIN_INTERVAL_MS = 15 * 60 * 1000;

// The operator's website account: the bot's sync channel (routes/sync.js)
// writes this user's LIVE account data directly into the DB. For this user the
// gateway paper portfolio must never be fetched or written through — it would
// pollute the live equity curve with paper-tracker numbers.
const BOT_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

async function operatorPortfolio(userId) {
  const [snaps] = await pool.execute(
    'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]);
  const [open] = await pool.execute(
    "SELECT * FROM trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC",
    [userId]);
  const [pnlRows] = await pool.execute(
    'SELECT COALESCE(SUM(pnl), 0) as net_pnl, COALESCE(SUM(fees), 0) as total_fees, COUNT(*) as total_trades FROM trades WHERE user_id = ? AND status = ?',
    [userId, 'CLOSED']);
  const [winRows] = await pool.execute(
    'SELECT COUNT(*) as wins FROM trades WHERE user_id = ? AND status = ? AND pnl > 0',
    [userId, 'CLOSED']);
  // Live/paper mode AND live-availability from the bot's own scan payload
  // (circuit_breaker). Mode and equity must be derived together so the header
  // can never say LIVE over a stale/paper number.
  let live = false;
  let cbUnavailable = false;
  let cbEquity = null;
  let cbFresh = false;
  try {
    const [rows] = await pool.execute('SELECT scan_json, updated_at FROM scan_cache WHERE id = 1');
    if (rows.length && rows[0].scan_json) {
      const scan = JSON.parse(rows[0].scan_json);
      const cb = scan.circuit_breaker || {};
      live = !!cb.live_mode;
      cbUnavailable = cb.live_unavailable === true;
      const ts = scan.received_at || rows[0].updated_at;
      cbFresh = !!ts && (Date.now() - new Date(ts).getTime()) < 30 * 60 * 1000;
      const e = parseFloat(cb.equity);
      if (Number.isFinite(e) && e > 0) cbEquity = e;
    }
  } catch (e) { /* mode stays PAPER */ }
  const total = parseInt(pnlRows[0]?.total_trades || 0);
  const wins = parseInt(winRows[0]?.wins || 0);
  const snap = snaps[0];
  const fresh = snap && (Date.now() - new Date(snap.snapshot_at).getTime()) < 30 * 60 * 1000;

  // In LIVE mode the operator has no paper baseline: show a REAL, fresh balance
  // or explicitly nothing. Two real sources, freshest first:
  //  1. the scan payload's executor-backed equity (cb.equity) — the bot
  //     refreshes it EVERY scan cycle;
  //  2. the close-driven equity snapshot — only written on live closes, so a
  //     quiet stretch with no closes previously read "BALANCE UNAVAILABLE"
  //     even while a perfectly fresh reading sat in the scan cache.
  // Only when neither source is fresh/real does the header say unavailable.
  let equity = snap ? parseFloat(snap.equity) : null;
  let liveUnavailable = false;
  if (live) {
    if (!cbUnavailable && cbFresh && cbEquity != null) {
      equity = cbEquity;
      // Write through so the operator's equity curve keeps growing between
      // closes (same change/staleness thresholds as the per-user path).
      try {
        const changed = !snap || Math.abs(parseFloat(snap.equity) - cbEquity) > 0.005;
        const staleSnap = !snap || (Date.now() - new Date(snap.snapshot_at).getTime()) > SNAPSHOT_MIN_INTERVAL_MS;
        if (changed || staleSnap) {
          await pool.execute(
            'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
            [userId, cbEquity, new Date()]);
        }
      } catch (e) { /* curve write-through is best-effort */ }
    } else if (cbUnavailable || !fresh) {
      equity = null;
      liveUnavailable = true;
    }
  }
  return {
    mode: live ? 'LIVE' : 'PAPER',
    source: 'sync',
    equity,
    live_unavailable: liveUnavailable,
    total_pnl: parseFloat(pnlRows[0]?.net_pnl || 0),
    daily_pnl: null, // not tracked on the sync path
    win_rate: total > 0 ? (wins / total) * 100 : null,
    total_trades: total,
    open_positions: open,
    closed_trades: [],
    linked: true,
    stale: !fresh,
  };
}

async function writeThrough(userId, pf) {
  // 1. Equity snapshot (only on change or staleness)
  if (pf.equity != null) {
    const [snaps] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
      [userId]);
    const last = snaps[0];
    const changed = !last || Math.abs(parseFloat(last.equity) - pf.equity) > 0.005;
    const stale = !last || (Date.now() - new Date(last.snapshot_at).getTime()) > SNAPSHOT_MIN_INTERVAL_MS;
    if (changed || stale) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [userId, pf.equity, new Date()]);
    }
  }

  // 2. Closed trades: insert only rows we haven't stored yet (preserves notes)
  const closed = pf.closed_trades || [];
  if (closed.length) {
    const [existing] = await pool.execute(
      `SELECT symbol, closed_at, pnl FROM trades WHERE user_id = ? AND status = 'CLOSED' ORDER BY closed_at DESC LIMIT ?`,
      [userId, 500]);
    const seen = new Set(existing.map(t =>
      `${t.symbol}|${new Date(t.closed_at).getTime()}|${parseFloat(t.pnl)}`));
    for (const t of closed) {
      const closedAt = t.closed_at ? new Date(t.closed_at) : new Date();
      const key = `${t.symbol}|${closedAt.getTime()}|${parseFloat(t.pnl)}`;
      if (seen.has(key)) continue;
      await pool.execute(
        `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
        [userId, t.symbol, t.direction, t.entry_price, t.exit_price,
         t.size_usd, t.pnl, t.commission || 0, t.strategy_type || null,
         t.opened_at ? new Date(t.opened_at) : new Date(), closedAt]);
    }
  }

  // 3. Open rows: replace wholesale (no user annotations on OPEN rows)
  await pool.execute(
    "DELETE FROM trades WHERE user_id = ? AND status = 'OPEN'", [userId]);
  for (const p of (pf.open_positions || [])) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit, opened_at)
       VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)`,
      [userId, p.symbol, p.direction, p.entry_price, p.size_usd,
       p.commission || 0, p.strategy_type || null,
       p.stop_loss || null, p.take_profit || null,
       p.opened_at ? new Date(p.opened_at) : new Date()]);
  }
}

async function dbFallback(userId) {
  const [snaps] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]);
  const [open] = await pool.execute(
    "SELECT * FROM trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC",
    [userId]);
  return {
    equity: snaps[0] ? parseFloat(snaps[0].equity) : null,
    open_positions: open,
    closed_trades: [],
    stale: true,
  };
}

// GET /api/portfolio
router.get('/', pfLimit, async (req, res) => {
  const userId = req.user.user_id;
  try {
    if (userId === BOT_USER_ID) {
      // Operator: authoritative live data from the sync channel, never the
      // gateway paper portfolio (and never a paper write-through).
      return res.json(await operatorPortfolio(userId));
    }
    if (!gateway.isConfigured()) {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER' });
    }
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/portfolio?telegram_id=${encodeURIComponent(ident.id)}`, 15000);
    if (r.status !== 200) {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER' });
    }
    const pf = r.data;
    try {
      await writeThrough(userId, pf);
    } catch (err) {
      console.error('Portfolio write-through error:', err.message);
    }
    return res.json({ ...pf, linked: ident.linked, stale: false });
  } catch (err) {
    console.error('Portfolio proxy error:', err.message);
    try {
      const fb = await dbFallback(userId);
      return res.json({ ...fb, mode: 'PAPER' });
    } catch (e) {
      return res.status(502).json({ error: 'Portfolio unavailable' });
    }
  }
});

module.exports = router;
