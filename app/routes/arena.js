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
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [userId]);
  return rows;
}

// Practice-follow sweep: mirror unprocessed engine signals into this PAPER
// account at the live mark (lazy — runs on account reads, no background job).
// Returns the refreshed { positions, balance } after any opens.
const followLib = require('../lib/arena_follow');
async function sweepFollows(userId, positions, marks) {
  const [fr] = await pool.execute('SELECT user_id, enabled, margin, leverage, last_signal_id FROM arena_follows WHERE user_id = ?', [userId]);
  const follow = fr[0];
  if (!follow || !Number(follow.enabled)) return { follow: follow || null, positions };
  const [sigs] = await pool.execute(
    'SELECT id, symbol, direction, stop_loss, take_profit FROM signals WHERE id > ? ORDER BY id ASC LIMIT ?',
    [Number(follow.last_signal_id) || 0, 5]);
  if (!sigs.length) return { follow, positions };
  const acct = await loadAccount(userId);
  const plan = followLib.planFollows({ signals: sigs, positions, balance: acct.balance,
    prefs: { margin: follow.margin, leverage: follow.leverage }, marks });
  let bal = acct.balance;
  for (const o of plan.opens) {
    // Inherit the SIGNAL's own stop/target when they're valid against the
    // live fill — practice-follow teaches the engine's real exits. An exit
    // level the market already passed is dropped (never a fake fill).
    const sig = sigs.find((s) => Number(s.id) === o.signal_id) || {};
    const tsTp = arena.validateTpSl(o.direction, o.price, sig.take_profit, null);
    const tsSl = arena.validateTpSl(o.direction, o.price, null, sig.stop_loss);
    const tp = tsTp.ok ? tsTp.data.tp : null;
    const sl = tsSl.ok ? tsSl.data.sl : null;
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, o.symbol, o.direction, o.price, o.margin, o.leverage, 'signal', tp, sl, new Date()]);
    bal -= o.margin;
  }
  if (plan.opens.length) {
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?', [Math.round(bal * 100) / 100, userId]);
  }
  if (plan.last_id > (Number(follow.last_signal_id) || 0)) {
    await pool.execute('UPDATE arena_follows SET last_signal_id = ? WHERE user_id = ?', [plan.last_id, userId]);
  }
  return { follow, positions: await loadPositions(userId) };
}

// Settle automatic exits: liquidation (the margin is gone, no credit),
// stop-loss and take-profit (closed at the trigger price, margin + pnl
// credited back). One pass per account read; returns the survivors.
async function settleLiquidations(userId, positions, marks) {
  const alive = [];
  let credit = 0;
  for (const p of positions) {
    const mark = marks[p.symbol] && Number(marks[p.symbol].price);
    const exit = mark > 0 ? arena.exitCheck(p, mark) : null;
    if (!exit) { alive.push(p); continue; }
    const pnl = exit.reason === 'liquidated' ? -p.margin : arena.posPnl(p, exit.price);
    await pool.execute(
      'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, p.symbol, p.direction, p.entry, round2(exit.price),
        p.margin, p.leverage, round2(pnl), exit.reason, p.opened_at, new Date()]);
    await pool.execute(
      'DELETE FROM arena_positions WHERE id = ? AND user_id = ?', [p.id, userId]);
    if (exit.reason !== 'liquidated') credit += p.margin + pnl;
  }
  if (credit !== 0) {
    const acct = await loadAccount(userId);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance + credit), userId]);
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
    const swept = await sweepFollows(userId, positions, marks);
    positions = swept.positions;
    // Re-read the balance — the sweep may have opened signal positions.
    const fresh = await loadAccount(userId);
    acct.balance = fresh.balance;
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
          source: p.source || 'manual',
          tp: p.tp == null ? null : p.tp,
          sl: p.sl == null ? null : p.sl,
          opened_at: p.opened_at,
        };
      }),
      follow: swept.follow ? { enabled: !!Number(swept.follow.enabled),
        margin: swept.follow.margin, leverage: swept.follow.leverage } : null,
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
    // Optional TP/SL — validated against the actual fill price.
    const ts = arena.validateTpSl(v.data.direction, price, (req.body || {}).tp, (req.body || {}).sl);
    if (!ts.ok) return res.status(400).json({ error: ts.error });
    await pool.execute(
      'INSERT INTO arena_positions (user_id, symbol, direction, entry, margin, leverage, source, tp, sl, opened_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      [userId, v.data.symbol, v.data.direction, price, v.data.margin, v.data.leverage, 'manual', ts.data.tp, ts.data.sl, new Date()]);
    await pool.execute('UPDATE arena_accounts SET balance = ? WHERE user_id = ?',
      [round2(acct.balance - v.data.margin), userId]);
    res.json({ ok: true, filled: { symbol: v.data.symbol, direction: v.data.direction, entry: price, margin: v.data.margin, leverage: v.data.leverage, tp: ts.data.tp, sl: ts.data.sl } });
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

// POST /api/arena/follow { enabled, margin, leverage } — practice-follow the
// engine's signal stream into this PAPER account. §4: paper only, revocable
// any time; enabling starts from the CURRENT newest signal (never back-fills
// old calls, which would fake a history).
router.post('/follow', authMiddleware, tradeLimit, async (req, res) => {
  try {
    const userId = req.user.user_id;
    const v = followLib.validateFollow(req.body);
    if (!v.ok) return res.status(400).json({ error: v.error });
    await loadAccount(userId);   // ensure the paper account exists
    // Start strictly from now: the newest existing signal id.
    let lastId = 0;
    try {
      const [latest] = await pool.execute(
        'SELECT id, symbol, direction FROM signals ORDER BY created_at DESC LIMIT ?', [1]);
      lastId = latest[0] ? Number(latest[0].id) || 0 : 0;
    } catch (e) { /* empty stream — start at 0 */ }
    await pool.execute(
      'INSERT INTO arena_follows (user_id, enabled, margin, leverage, last_signal_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
      [userId, v.data.enabled ? 1 : 0, v.data.margin, v.data.leverage, lastId, new Date()]);
    res.json({ ok: true, follow: v.data, virtual: true });
  } catch (err) {
    console.error('Arena follow error:', err.message);
    res.status(500).json({ error: 'Follow update failed' });
  }
});

// GET /api/arena/trader/:handle — PUBLIC trader card for an opted-in handle.
// §4: percent / count / badges only — never an amount, not even virtual.
const traderLib = require('../lib/arena_trader');
router.get('/trader/:handle', async (req, res) => {
  try {
    const handle = String(req.params.handle || '').trim();
    if (!traderLib.HANDLE_RE.test(handle)) return res.status(400).json({ error: 'Invalid handle' });
    const [u] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
    if (!u[0]) return res.status(404).json({ error: 'No such trader' });
    const userId = u[0].id;
    const [acct] = await pool.execute('SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
    if (!acct[0]) return res.status(404).json({ error: 'No arena account' });
    const positions = await loadPositions(userId);
    const [trades] = await pool.execute(
      'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30', [userId]);
    let marks = {};
    try { marks = await getTickers(); } catch (e) { /* percent renders from balance */ }
    res.json(traderLib.buildTraderCard({ handle, balance: acct[0].balance, positions, marks, trades }));
  } catch (err) {
    console.error('Arena trader error:', err.message);
    res.status(500).json({ error: 'Trader card unavailable' });
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

// GET /api/arena/seasons — PUBLIC Hall of Champions: every ENDED season with
// its final podium (top 3). §4: opt-in handles + percent only, same as every
// arena board. Ended standings are immutable (the window is closed), so this
// is the permanent record.
router.get('/seasons', async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, name, starts_at, ends_at FROM arena_seasons');
    const now = new Date();
    const ended = rows.filter((s) => seasons.seasonStatus(s, now) === 'ended').slice(0, 12);
    if (!ended.length) return res.json({ seasons: [] });
    const [handles] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
    const handleOf = new Map(handles.map((h) => [h.id, h.leaderboard_handle]));
    const out = [];
    for (const s of ended) {
      const [trades] = await pool.execute(
        'SELECT user_id, pnl FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
        [s.starts_at, s.ends_at]);
      out.push({
        name: s.name, starts_at: s.starts_at, ends_at: s.ends_at,
        podium: seasons.seasonRanking(trades, handleOf).slice(0, 3),
      });
    }
    res.json({ seasons: out, virtual: true });
  } catch (err) {
    console.error('Arena seasons history error:', err.message);
    res.status(500).json({ error: 'Hall unavailable' });
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
