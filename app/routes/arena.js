'use strict';
/**
 * Paper Trading Arena — /api/arena. Every registered user gets a virtual
 * account with the same starting stake the moment they touch the Arena: no
 * exchange API keys, no bot gateway, no setup. Fills and marks come from the
 * public Bitget ticker feed (lib/tickers) and positions live in the app DB, so
 * the Arena works even when the trading engine is offline — the zero-friction
 * on-ramp and the substrate for paper-trading competitions.
 *
 * Mechanics are lib/arena.js (pure, tested): isolated margin, pnl clamped at
 * -margin, liquidation at -(1-MMR) return on margin. Liquidations settle
 * lazily whenever the account is read or traded against.
 *
 * §4: virtual funds only — nothing here can move real money. The PUBLIC
 * leaderboard shows opt-in anonymous handles + percent return only (the same
 * privacy model as the main leaderboard); virtual balances appear solely on
 * the owner's private account view.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { pool } = require('../db');
const { getTickers } = require('../lib/tickers');
const arena = require('../lib/arena');

const router = express.Router();

const tradeLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });

const round2 = (n) => Math.round((Number(n) || 0) * 100) / 100;

async function loadAccount(userId) {
  const [rows] = await pool.execute(
    'SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
  if (rows[0]) return rows[0];
  await pool.execute(
    'INSERT INTO arena_accounts (user_id, balance, created_at) VALUES (?, ?, ?)',
    [userId, arena.START_BALANCE, new Date()]);
  return { user_id: userId, balance: arena.START_BALANCE };
}

async function loadPositions(userId) {
  const [rows] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [userId]);
  return rows;
}

// Settle any liquidated positions at their liq price: the margin is gone
// (pnl = -margin, the isolated-margin floor), the position closes, a history
// row records it. Returns the surviving positions.
async function settleLiquidations(userId, positions, marks) {
  const alive = [];
  for (const p of positions) {
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    if (mark > 0 && arena.isLiquidated(p, mark)) {
      await pool.execute(
        'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
        [userId, p.symbol, p.direction, p.entry, round2(arena.liqPrice(p)),
          p.margin, p.leverage, -p.margin, 'liquidated', p.opened_at, new Date()]);
      await pool.execute(
        'DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    } else {
      alive.push(p);
    }
  }
  return alive;
}

// GET /api/arena/account — the owner's private account view (auto-provisions).
router.get('/account', authMiddleware, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const acct = await loadAccount(userId);
    let marks = {};
    try { marks = await getTickers(); } catch (e) { /* stale-mark view below */ }
    let positions = await loadPositions(userId);
    positions = await settleLiquidations(userId, positions, marks);
    const [history] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30', [userId]);
    const eq = arena.equity(acct.balance, positions, marks);
    res.json({
      start_balance: arena.START_BALANCE,
      balance: round2(acct.balance),
      equity: round2(eq),
      return_pct: round2(arena.returnPct(eq)),
      limits: { min_margin: arena.MIN_MARGIN, max_leverage: arena.MAX_LEVERAGE, max_open: arena.MAX_OPEN },
      positions: positions.map((p) => {
        const mark = marks[p.symbol] && Number(marks[p.symbol].price);
        const pnl = mark > 0 ? arena.posPnl(p, mark) : null;
        return {
          id: p.id, symbol: p.symbol, direction: p.direction,
          entry: p.entry, mark: mark > 0 ? mark : null,
          margin: p.margin, leverage: p.leverage,
          pnl: pnl == null ? null : round2(pnl),
          pnl_pct: pnl == null ? null : round2(pnl / p.margin * 100),
          liq_price: arena.liqPrice(p),
          opened_at: p.opened_at,
        };
      }),
      history,
      badges: require('../lib/arena_badges').computeArenaBadges({
        trades: history, returnPct: arena.returnPct(eq) }),
      virtual: true,   // §4: this account holds no real funds
    });
  } catch (err) {
    console.error('Arena account error:', err.message);
    res.status(500).json({ error: 'Arena unavailable' });
  }
});

// POST /api/arena/open { symbol, direction, margin, leverage } — market fill
// at the live public ticker price.
router.post('/open', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const acct = await loadAccount(userId);
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return res.status(503).json({ error: 'Market data unavailable — try again shortly' });
    }
    let positions = await loadPositions(userId);
    positions = await settleLiquidations(userId, positions, marks);
    const v = arena.validateOpen(req.body, acct.balance, positions.length);
    if (!v.ok) return res.status(400).json({ error: v.error });
    const t = marks[v.data.symbol];
    const price = t && Number(t.price);
    if (!(price > 0)) return res.status(400).json({ error: 'Unknown symbol — use a listed USDT-M pair like BTCUSDT' });
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?)',
      [userId, v.data.symbol, v.data.direction, price, v.data.margin, v.data.leverage, new Date()]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance - v.data.margin), userId]);
    res.json({ ok: true, filled: { symbol: v.data.symbol, direction: v.data.direction, entry: price, margin: v.data.margin, leverage: v.data.leverage } });
  } catch (err) {
    console.error('Arena open error:', err.message);
    res.status(500).json({ error: 'Arena unavailable' });
  }
});

// POST /api/arena/close { position_id } — close at the live mark.
router.post('/close', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const posId = Number((req.body || {}).position_id);
    if (!Number.isInteger(posId) || posId <= 0) {
      return res.status(400).json({ error: 'Invalid position_id' });
    }
    const [rows] = await pool.execute(
      'SELECT id, user_id, symbol, direction, entry, margin, leverage, opened_at FROM arena_positions WHERE id = ? AND user_id = ?', [posId, userId]);
    const p = rows[0];
    if (!p) return res.status(404).json({ error: 'Position not found' });
    let marks;
    try { marks = await getTickers(); } catch (e) {
      return res.status(503).json({ error: 'Market data unavailable — try again shortly' });
    }
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    if (!(mark > 0)) return res.status(503).json({ error: 'No live mark for this symbol — try again shortly' });
    const liquidated = arena.isLiquidated(p, mark);
    const exitPrice = liquidated ? round2(arena.liqPrice(p)) : mark;
    const pnl = liquidated ? -p.margin : arena.posPnl(p, mark);
    const acct = await loadAccount(userId);
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, p.symbol, p.direction, p.entry, exitPrice, p.margin, p.leverage,
        round2(pnl), liquidated ? 'liquidated' : 'manual', p.opened_at, new Date()]);
    await pool.execute('DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + p.margin + pnl), userId]);
    res.json({ ok: true, closed: { symbol: p.symbol, pnl: round2(pnl), exit_price: exitPrice, liquidated } });
  } catch (err) {
    console.error('Arena close error:', err.message);
    res.status(500).json({ error: 'Arena unavailable' });
  }
});

// GET /api/arena/leaderboard — PUBLIC. Opt-in handles + percent return only
// (§4: no balances, no dollar figures — not even virtual ones).
router.get('/leaderboard', async (req, res) => {
  try {
    const [accounts] = await pool.execute('SELECT user_id, balance FROM arena_accounts');
    if (!accounts.length) return res.json({ rows: [], ranked_total: 0 });
    const [allPos] = await pool.execute(
      'SELECT id, user_id, symbol, direction, entry, margin, leverage FROM arena_positions');
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const [tradeCounts] = await pool.execute(
      'SELECT user_id, COUNT(*) AS n FROM arena_trades GROUP BY user_id');
    const countOf = new Map(tradeCounts.map((t) => [t.user_id, t.n]));
    let marks = {};
    try { marks = await getTickers(); } catch (e) { /* rank on balances only */ }
    const posOf = new Map();
    for (const p of allPos) {
      if (!posOf.has(p.user_id)) posOf.set(p.user_id, []);
      posOf.get(p.user_id).push(p);
    }
    const rows = accounts
      .filter((a) => handleOf.has(a.user_id))
      .map((a) => {
        const eq = arena.equity(a.balance, posOf.get(a.user_id) || [], marks);
        return {
          handle: handleOf.get(a.user_id),
          return_pct: round2(arena.returnPct(eq)),
          trades: (countOf.get(a.user_id) || 0) + (posOf.get(a.user_id) || []).length,
        };
      })
      .sort((x, y) => y.return_pct - x.return_pct)
      .slice(0, 50)
      .map((r, i) => ({ rank: i + 1, ...r }));
    res.json({ rows, ranked_total: rows.length, virtual: true });
  } catch (err) {
    console.error('Arena leaderboard error:', err.message);
    res.status(500).json({ error: 'Leaderboard unavailable' });
  }
});

// ---- Competition seasons ------------------------------------------------
const seasons = require('../lib/arena_seasons');

// GET /api/arena/season — PUBLIC. The most recently authored season with its
// live status and (once it has started) the in-window standings. A season is
// a time window, never a reset — the all-time board keeps running.
router.get('/season', async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at FROM arena_seasons');
    const season = rows[0];
    if (!season) return res.json({ season: null });
    const status = seasons.seasonStatus(season, new Date());
    const out = {
      season: { name: season.name, starts_at: season.starts_at, ends_at: season.ends_at, status },
      virtual: true,
    };
    if (status !== 'upcoming') {
      const [trades] = await pool.execute(
        'SELECT user_id, pnl FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
        [season.starts_at, season.ends_at]);
      const [handles] = await pool.execute(
        'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
      out.rows = seasons.seasonRanking(trades, new Map(handles.map((h) => [h.id, h.leaderboard_handle])));
    }
    res.json(out);
  } catch (err) {
    console.error('Arena season error:', err.message);
    res.status(500).json({ error: 'Season unavailable' });
  }
});

// POST /api/arena/season { name, starts_at, ends_at } — operator only.
router.post('/season', authMiddleware, async (req, res) => {
  try {
    const [u] = await pool.execute('SELECT plan FROM users WHERE id = ?', [req.user.user_id]);
    if (!u[0] || String(u[0].plan) !== 'admin') {
      return res.status(403).json({ error: 'admin_required', detail: 'Only the operator can author a season.' });
    }
    const v = seasons.validateSeason(req.body);
    if (!v.ok) return res.status(400).json({ error: v.error });
    await pool.execute(
      'INSERT INTO arena_seasons (name, starts_at, ends_at, created_at) VALUES (?, ?, ?, ?)',
      [v.data.name, v.data.starts_at, v.data.ends_at, new Date()]);
    res.json({ ok: true, season: { name: v.data.name, starts_at: v.data.starts_at, ends_at: v.data.ends_at } });
  } catch (err) {
    console.error('Arena season create error:', err.message);
    res.status(500).json({ error: 'Season create failed' });
  }
});

module.exports = router;
