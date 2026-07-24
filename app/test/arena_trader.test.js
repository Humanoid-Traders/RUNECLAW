'use strict';
/**
 * Public Arena trader cards — /trader/:handle. §4 is the whole design: the
 * public payload is percent / count / badge ONLY — never an amount, not even
 * a virtual one, and never a user_id or email. SSR-lite injects the validated
 * handle into title/og so shared links unfurl personally.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('fs');
const path = require('path');
const authModule = require('../auth');
const { buildTraderCard } = require('../lib/arena_trader');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

test('buildTraderCard is percent/count/badge only — no amounts, ids or emails', () => {
  const card = buildTraderCard({
    handle: 'ace', balance: 10500,
    positions: [{ symbol: 'BTCUSDT', margin: 500, leverage: 2, direction: 'LONG', entry: 100 }],
    marks: { BTCUSDT: { price: 100 } },
    trades: [{ symbol: 'BTCUSDT', direction: 'LONG', leverage: 5, margin: 1000, pnl: 500, reason: 'manual', closed_at: new Date() }],
  });
  assert.equal(card.handle, 'ace');
  assert.equal(card.recent[0].ret_pct, 50);           // 500 on 1000 margin
  assert.equal(card.win_rate_pct, 100);
  const blob = JSON.stringify(card).toLowerCase();
  for (const needle of ['balance', 'equity', 'user_id', 'email', 'margin', '"pnl"', 'vusdt']) {
    assert.ok(!blob.includes(needle), `card must not contain "${needle}"`);
  }
});

let server, base;
test.before(async () => {
  setTickerFetcher(async () => ({ BTCUSDT: { price: 100, change: 0, volume: 1 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('the public trader endpoint resolves an opted-in handle, 404s otherwise', async () => {
  const bad = await req('GET', '/api/arena/trader/ghost_nobody');
  assert.equal(bad.status, 404);
  const reg = await req('POST', '/api/auth/register', { body: { email: 'trader1@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  await req('GET', '/api/arena/account', { token });                          // provision
  await req('POST', '/api/leaderboard/opt-in', { token, body: { handle: 'card_ace' } });
  const r = await req('GET', '/api/arena/trader/card_ace');
  assert.equal(r.status, 200);
  assert.equal(r.data.handle, 'card_ace');
  assert.equal(r.data.virtual, true);
  assert.ok(!/user_id|email|balance/i.test(JSON.stringify(r.data)));
});

test('the /trader page + SSR-lite route + board links are wired', () => {
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'trader.html'), 'utf8');
  const arenaPage = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(srv, /app\.get\('\/trader\/:handle'/);
  assert.match(srv, /__HANDLE__/);
  assert.match(page, /__HANDLE__/);                     // og/title tokens
  assert.match(page, /api\/arena\/trader\//);
  assert.match(page, /no dollar figures/i);
  assert.match(page, /Challenge them in the Arena/);
  assert.match(arenaPage, /\/trader\/' \+ encodeURIComponent/);
});
