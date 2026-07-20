'use strict';
/**
 * Portfolio intelligence (PR FF) — alpha vs holding, expectancy, payoff,
 * drawdown, streaks. Everything derives from recorded closed trades only:
 * the buy-and-hold benchmark is each trade's own entry/exit move, so a row
 * without usable prices sits out the alpha comparison (counted, not guessed).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { computeIntel } = require('../lib/intel');
const { composeLetter, composePublicLetter, lastCompletedWeek } = require('../lib/letter');
const { pool } = require('../db');
const authModule = require('../auth');

function closed(over) {
  return Object.assign({
    symbol: 'BTC/USDT:USDT', direction: 'LONG', entry_price: 100, exit_price: 110,
    pnl: 10, fees: 0.1, size_usd: 100, opened_at: '2026-07-13T01:00:00Z',
    closed_at: '2026-07-13T02:00:00Z',
  }, over);
}

// ── Pure math ────────────────────────────────────────────────────────────────

test('long alpha: agent return minus the asset\'s own move', () => {
  // Agent made 20% on notional while the asset moved +10% -> alpha +10%.
  const r = computeIntel([closed({ pnl: 20, size_usd: 100, entry_price: 100, exit_price: 110 })]);
  assert.equal(r.alpha.priced, 1);
  assert.equal(r.alpha.mean_alpha_pct, 10);
  assert.equal(r.alpha.beat_market, 1);
});

test('short alpha in a falling market is large and positive', () => {
  // Short: asset fell 20% (holding = -20%), agent booked +18% -> alpha +38%.
  const r = computeIntel([closed({
    direction: 'SHORT', pnl: 18, size_usd: 100, entry_price: 100, exit_price: 80,
  })]);
  assert.equal(r.alpha.mean_alpha_pct, 38);
  assert.equal(r.alpha.best.symbol, 'BTC');
});

test('rows without usable prices sit out alpha but still count for PnL stats', () => {
  const r = computeIntel([
    closed({ pnl: 10 }),
    closed({ pnl: -5, entry_price: null, exit_price: null }),
    closed({ pnl: 3, exit_price: 0 }),          // zero/absent exit -> unpriced
    closed({ pnl: 7, size_usd: 0 }),            // no notional -> fully skipped
  ]);
  assert.equal(r.trades, 3);
  assert.equal(r.skipped, 1);
  assert.equal(r.alpha.priced, 1);
  assert.equal(r.alpha.unpriced, 2);
  assert.equal(r.net_pnl_usd, 8);
});

test('drawdown, streaks, expectancy, payoff from the cumulative record', () => {
  const r = computeIntel([
    closed({ pnl: 10 }), closed({ pnl: 20 }),          // peak cum = 30
    closed({ pnl: -25 }), closed({ pnl: -5 }),         // trough cum = 0 -> dd 30
    closed({ pnl: 40 }),
  ]);
  assert.equal(r.max_drawdown_usd, 30);
  assert.equal(r.longest_win_streak, 2);
  assert.equal(r.longest_loss_streak, 2);
  assert.equal(r.expectancy_usd, 8);                   // 40 / 5
  assert.equal(r.payoff_ratio, 1.56);                  // avg win 23.33 / avg loss 15
  assert.equal(r.profit_factor, 2.33);
});

test('empty history yields nulls, never fabricated zeros-with-meaning', () => {
  const r = computeIntel([]);
  assert.equal(r.trades, 0);
  assert.equal(r.win_rate_pct, null);
  assert.equal(r.expectancy_usd, null);
  assert.equal(r.alpha, null);
});

// ── Letter integration (percent-only on the public side) ─────────────────────

test('both letters carry the alpha section; the public one stays dollar-free', () => {
  const week = lastCompletedWeek();
  const data = {
    trades: [
      closed({ pnl: 20, size_usd: 100, entry_price: 100, exit_price: 110, closed_at: week.start.toISOString() }),
      closed({ pnl: -4, size_usd: 100, entry_price: 50, exit_price: 49, closed_at: week.start.toISOString() }),
    ],
    equity: { start: 1000, end: 1016 },
    signals: [], openCount: 0, reports: null,
  };
  for (const compose of [composeLetter, composePublicLetter]) {
    const letter = compose(week, data);
    const alpha = (letter.sections || []).find(s => s.title === 'Alpha vs holding');
    assert.ok(alpha, `${compose.name} has the alpha section`);
    assert.match(alpha.html, /pure alpha/);
    assert.ok(!alpha.html.includes('$'), 'alpha section is percent-only by construction');
  }
  // The public letter's privacy line holds across ALL sections.
  const publicLetter = composePublicLetter(week, data);
  for (const s of publicLetter.sections) {
    assert.ok(!String(s.html).includes('$'), `no dollars in public section "${s.title}"`);
  }
});

test('letters without priced trades simply omit the alpha section', () => {
  const week = lastCompletedWeek();
  const data = {
    trades: [closed({ entry_price: null, exit_price: null, closed_at: week.start.toISOString() })],
    equity: { start: null, end: null }, signals: [], openCount: 0, reports: null,
  };
  const letter = composePublicLetter(week, data);
  assert.ok(!letter.sections.some(s => s.title === 'Alpha vs holding'));
});

// ── Route (JWT-authed, own trades only) ──────────────────────────────────────

let server, base;

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/portfolio', require('../routes/portfolio'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('GET /api/portfolio/intel requires auth and returns only own-trade analytics', async () => {
  assert.equal((await req('GET', '/api/portfolio/intel')).status, 401);

  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'intel1@test.io', password: 'x'.repeat(12) } });
  assert.equal(reg.status, 200);
  const token = reg.data.token;
  const uid = reg.data.user_id;

  // Seed two closed trades through the same INSERT shape the sync uses.
  const ins = `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`;
  await pool.execute(ins, [uid, 'ETH/USDT:USDT', 'LONG', 2000, 2100, 100, 12, 0.1,
    'breakout', new Date('2026-07-13T01:00:00Z'), new Date('2026-07-13T04:00:00Z')]);
  await pool.execute(ins, [uid, 'SOL/USDT:USDT', 'SHORT', 200, 190, 100, 4, 0.1,
    'breakdown', new Date('2026-07-14T01:00:00Z'), new Date('2026-07-14T02:00:00Z')]);

  const r = await req('GET', '/api/portfolio/intel', { token });
  assert.equal(r.status, 200);
  const d = r.data.intel;
  assert.equal(d.trades, 2);
  assert.equal(d.wins, 2);
  assert.equal(d.alpha.priced, 2);
  // ETH long: 12% - 5% = 7 ; SOL short: 4% - (-5%) = 9 -> mean 8.
  assert.equal(d.alpha.mean_alpha_pct, 8);
  assert.equal(d.alpha.beat_market, 2);

  // A different account sees ITS OWN (empty) history, never someone else's.
  const reg2 = await req('POST', '/api/auth/register',
    { body: { email: 'intel2@test.io', password: 'x'.repeat(12) } });
  const r2 = await req('GET', '/api/portfolio/intel', { token: reg2.data.token });
  assert.equal(r2.status, 200);
  assert.equal(r2.data.intel.trades, 0);
});
