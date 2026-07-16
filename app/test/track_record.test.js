'use strict';
/**
 * Public track record — verifiable numbers only, from synced operator data.
 * Runs against the MemoryDB fallback. Endpoint-driven, no auth required.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');

let server, base;

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${path}`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    }).on('error', reject);
  });
}

const OPERATOR = 1;

async function seedClose(symbol, pnl, closedAt) {
  await pool.execute(
    "INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,'CLOSED',?,?,?)",
    [OPERATOR, symbol, 'LONG', 100, 105, 200, pnl, 0.3, 'test', closedAt, closedAt]);
}

test.before(async () => {
  const app = express();
  app.use('/api/public', require('../routes/track'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('empty account reports nulls — never invented numbers', async () => {
  const r = await get('/api/public/track-record');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.stats.trades, 0);
  assert.strictEqual(r.data.stats.net_pnl_usd, null);
  assert.strictEqual(r.data.stats.profit_factor, null);
  assert.strictEqual(r.data.stats.max_drawdown_pct, null);
  assert.strictEqual(r.data.mode, null);            // no scan cache -> unknown
  assert.deepStrictEqual(r.data.equity_curve, []);
});

test('aggregates PF, win rate, monthly buckets, drawdown from synced data', async () => {
  // Response cache is 5 min; restart the router state by re-requiring fresh.
  delete require.cache[require.resolve('../routes/track')];
  const app = express();
  app.use('/api/public', require('../routes/track'));
  const srv = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const b = `http://127.0.0.1:${srv.address().port}`;

  await seedClose('BTC/USDT', 30, new Date('2026-06-10T10:00:00Z'));
  await seedClose('ETH/USDT', -10, new Date('2026-07-02T10:00:00Z'));
  await seedClose('SOL/USDT', 20, new Date('2026-07-05T10:00:00Z'));
  for (const [eq, at] of [[100, '2026-06-01'], [140, '2026-06-20'], [120, '2026-07-01'], [141, '2026-07-10']]) {
    await pool.execute(
      'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
      [OPERATOR, eq, new Date(at)]);
  }
  await pool.execute('REPLACE INTO scan_cache (scan_json) VALUES (?)',
    [JSON.stringify({ circuit_breaker: { live_mode: true, equity: 141 } })]);

  const r = await new Promise((resolve, reject) => {
    http.get(`${b}/api/public/track-record`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d) }));
    }).on('error', reject);
  });
  srv.close();

  assert.strictEqual(r.status, 200);
  const s = r.data.stats;
  assert.strictEqual(s.trades, 3);
  assert.strictEqual(s.wins, 2);
  assert.strictEqual(s.net_pnl_usd, 40);
  assert.strictEqual(s.profit_factor, 5);                 // 50 gross win / 10 gross loss
  assert.strictEqual(r.data.mode, 'LIVE');                // from scan cache, not guessed
  // Monthly buckets by close month (UTC).
  assert.strictEqual(r.data.monthly_pnl_usd['2026-06'], 30);
  assert.strictEqual(r.data.monthly_pnl_usd['2026-07'], 10);
  // Drawdown: peak 140 -> trough 120 = 14.29%.
  assert.ok(Math.abs(s.max_drawdown_pct - 14.29) < 0.01);
  assert.strictEqual(s.current_equity_usd, 141);
  assert.strictEqual(r.data.recent_trades[0].symbol, 'SOL/USDT'); // newest first
});
