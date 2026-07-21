'use strict';
/**
 * Leaderboard — opt-in (anonymous handle), ranked by return %, no dollar leak.
 * Runs against the MemoryDB fallback. Endpoint-driven.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');

let server, base;

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
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

let n = 0;
const reg = () => req('POST', '/api/auth/register',
  { body: { email: `lb${++n}@test.io`, password: 'x'.repeat(12) } });

async function seedClosedTrade(uid, pnl) {
  await pool.execute(
    "INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,'CLOSED',?,?,?)",
    [uid, 'SOL/USDT', 'LONG', 100, 105, 200, pnl, 0.2, 'test', new Date(), new Date()]);
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('opt-in needs a valid, unique handle; board ranks by return %, no dollars', async () => {
  const a = await reg();
  const b = await reg();

  // Too-short handle is rejected.
  let r = await req('POST', '/api/leaderboard/opt-in', { token: a.data.token, body: { handle: 'no' } });
  assert.strictEqual(r.status, 400);

  // A joins.
  r = await req('POST', '/api/leaderboard/opt-in', { token: a.data.token, body: { handle: 'alice' } });
  assert.strictEqual(r.status, 200);

  // B cannot take the same handle (case-insensitive).
  r = await req('POST', '/api/leaderboard/opt-in', { token: b.data.token, body: { handle: 'ALICE' } });
  assert.strictEqual(r.status, 409);

  // A has a winning closed trade → +5% on the $10k paper stake.
  await seedClosedTrade(a.data.user_id, 500);

  r = await req('GET', '/api/leaderboard', { token: a.data.token });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.opted_in, true);
  assert.strictEqual(r.data.handle, 'alice');
  const me = r.data.rows.find(x => x.handle === 'alice');
  assert.ok(me, 'A appears on the board');
  assert.strictEqual(me.return_pct, 5);
  assert.strictEqual(me.trades, 1);
  assert.strictEqual(me.is_me, true);
  // No dollar amount ever leaks into a row.
  for (const k of ['net_pnl', 'pnl', 'equity', 'balance', 'size_usd']) {
    assert.ok(!(k in me), `row must not expose ${k}`);
  }
});

test('opt-out removes the user from the board', async () => {
  const a = await reg();
  await req('POST', '/api/leaderboard/opt-in', { token: a.data.token, body: { handle: 'bob' } });
  await seedClosedTrade(a.data.user_id, 100);
  let r = await req('GET', '/api/leaderboard', { token: a.data.token });
  assert.ok(r.data.rows.some(x => x.handle === 'bob'));
  await req('POST', '/api/leaderboard/opt-out', { token: a.data.token });
  r = await req('GET', '/api/leaderboard', { token: a.data.token });
  assert.ok(!r.data.rows.some(x => x.handle === 'bob'));
  assert.strictEqual(r.data.opted_in, false);
});

test('leaderboard requires auth', async () => {
  const r = await req('GET', '/api/leaderboard');
  assert.strictEqual(r.status, 401);
});

test('UX-6: my_rank/ranked_total report a real position, even past the top window', async () => {
  // A ranked member sees their own rank and the total ranked count.
  const a = await reg();
  await req('POST', '/api/leaderboard/opt-in', { token: a.data.token, body: { handle: 'ranker' } });
  await seedClosedTrade(a.data.user_id, 300);
  let r = await req('GET', '/api/leaderboard', { token: a.data.token });
  assert.strictEqual(typeof r.data.my_rank, 'number');
  assert.ok(r.data.my_rank >= 1, 'a member with a closed trade has a numeric rank');
  assert.ok(r.data.ranked_total >= 1);
  assert.ok(r.data.ranked_total >= r.data.my_rank, 'total is never smaller than my own rank');
  // Rank is position-only — no dollar figure rides along on the payload.
  for (const k of ['net_pnl', 'my_pnl', 'equity', 'balance']) {
    assert.ok(!(k in r.data), `payload must not expose ${k}`);
  }

  // Opted in but no closed trade yet → not ranked (null), not a fake 0.
  const c = await reg();
  await req('POST', '/api/leaderboard/opt-in', { token: c.data.token, body: { handle: 'newbie' } });
  r = await req('GET', '/api/leaderboard', { token: c.data.token });
  assert.strictEqual(r.data.opted_in, true);
  assert.strictEqual(r.data.my_rank, null, 'no realized round-trip ⇒ unranked');
});
