/**
 * Outcome-Based Agent Reputation.
 *
 * Part 1 — pure scoring (app/lib/reputation.js): confidence pulls thin samples
 * toward neutral 50; a deep, disciplined, profitable record grades well; an
 * unprofitable / high-drawdown / high-fee record is flagged; determinism.
 * Part 2 — the JWT-authed /api/reputation route against MemoryDB.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'r'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const { computeReputation, sampleConfidence } = require('../lib/reputation');

// Build a synthetic closed-trade record. day is an index → distinct months when
// spread with `monthGap`.
function tr(pnl, { size = 100, fees = 0.1, y = 2025, m = 1, d = 1 } = {}) {
  const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}T00:00:00Z`;
  return { symbol: 'BTC/USDT:USDT', direction: 'LONG', pnl, size_usd: size, fees, opened_at: iso, closed_at: iso };
}

test('an empty history is unrated, not zero', () => {
  const r = computeReputation([]);
  assert.strictEqual(r.unrated, true);
  assert.strictEqual(r.score, null);
  assert.strictEqual(r.grade, null);
});

test('sampleConfidence rises with trade count and is bounded', () => {
  assert.ok(sampleConfidence(3) < sampleConfidence(20));
  assert.ok(sampleConfidence(20) < sampleConfidence(100));
  assert.strictEqual(sampleConfidence(1000), 100);
});

test('a thin sample is pulled toward neutral 50', () => {
  // Two great trades — but only two. Confidence is low, so the score cannot run
  // away from 50.
  const r = computeReputation([tr(50), tr(60)]);
  assert.ok(r.score > 40 && r.score < 65, `expected near-neutral, got ${r.score}`);
  assert.ok(r.flags.some(f => f.key === 'thin_sample'));
});

test('a deep, disciplined, profitable record grades well', () => {
  // 40 trades, ~70% winners, small controlled losses, spread across months.
  const rows = [];
  for (let i = 0; i < 40; i++) {
    const win = i % 10 < 7; // 70% win rate
    const month = (i % 8) + 1; // 8 distinct months
    rows.push(tr(win ? 12 : -6, { m: month, d: (i % 25) + 1, fees: 0.1 }));
  }
  const r = computeReputation(rows);
  assert.strictEqual(r.unrated, false);
  assert.ok(r.score >= 62, `expected a strong score, got ${r.score}`);
  assert.ok(['A', 'B', 'C'].includes(r.grade));
  assert.ok(r.subscores.performance >= 60);
});

test('an unprofitable record is flagged net-negative', () => {
  const rows = [];
  for (let i = 0; i < 30; i++) rows.push(tr(i % 3 === 0 ? 5 : -10, { m: (i % 6) + 1 }));
  const r = computeReputation(rows);
  assert.ok(r.flags.some(f => f.key === 'unprofitable'));
  assert.ok(r.score < 50, `expected a weak score, got ${r.score}`);
});

test('heavy fees produce a fee-drag flag and depress cost efficiency', () => {
  const rows = [];
  for (let i = 0; i < 30; i++) rows.push(tr(i % 2 ? 8 : -6, { m: (i % 6) + 1, fees: 6 })); // fees ~ pnl scale
  const r = computeReputation(rows);
  assert.ok(r.metrics.fee_drag_pct > 20);
  assert.ok(r.flags.some(f => f.key === 'high_fee_drag'));
});

test('scoring is deterministic', () => {
  const rows = [tr(10, { m: 1 }), tr(-4, { m: 2 }), tr(7, { m: 3 })];
  assert.deepStrictEqual(computeReputation(rows), computeReputation(rows));
});

// ── Part 2: the /api/reputation route against MemoryDB ───────────────────

function request(method, path, token) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${path}`, { method, headers: token ? { Authorization: `Bearer ${token}` } : {} }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve({ status: res.statusCode, raw: data }));
    });
    req.on('error', reject);
    req.end();
  });
}

let base, appServer, token;

test.before(async () => {
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)', ['repuser@test.io', 'x', 'Rep']);
  const [rows] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['repuser@test.io']);
  const uid = rows[0].id;
  token = jwt.sign({ user_id: uid, email: rows[0].email }, process.env.JWT_SECRET);

  const ins = 'INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)';
  await pool.execute(ins, [uid, 'BTC/USDT:USDT', 'LONG', 100, 112, 100, 12, 0.1, 'x', '2025-01-01T00:00:00Z', '2025-01-05T00:00:00Z']);
  await pool.execute(ins, [uid, 'ETH/USDT:USDT', 'LONG', 100, 94, 100, -6, 0.1, 'x', '2025-02-01T00:00:00Z', '2025-02-03T00:00:00Z']);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/reputation', require('../routes/reputation'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => { if (appServer) appServer.close(); });

test('GET /api/reputation requires a JWT', async () => {
  const r = await request('GET', '/api/reputation');
  assert.strictEqual(r.status, 401);
});

test('GET /api/reputation returns a scored, advisory readout', async () => {
  const r = await request('GET', '/api/reputation', token);
  assert.strictEqual(r.status, 200);
  const body = JSON.parse(r.raw);
  assert.strictEqual(body.unrated, false);
  assert.strictEqual(body.metrics.trades, 2);
  assert.ok(typeof body.score === 'number');
  assert.ok(['A', 'B', 'C', 'D', 'E'].includes(body.grade));
  assert.match(body.note, /never a verdict/i);
  assert.ok(Array.isArray(body.flags) && body.flags.length > 0);
});
