'use strict';
/**
 * Arena live tape — /api/arena/tape is the public trading-floor feed: the
 * latest closed paper trades from OPTED-IN handles only, percent return on
 * margin, newest first. §4: no dollar amounts (not even virtual vUSDT), no
 * balances, no user ids; traders without a leaderboard handle never appear.
 * The pulse line is counts only (traders / closes in 24h).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const authModule = require('../auth');
const { setTickerFetcher } = require('../lib/tickers');

let server, base;
let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };

test.before(async () => {
  setTickerFetcher(async () => PRICES);
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
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', { body: { email: `tape${seq}@example.com`, password: 'longenough1' } });
  assert.equal(r.status, 200);
  return r.data.token;
}

test('the tape is public and starts quiet with count-only pulse fields', async () => {
  const r = await req('GET', '/api/arena/tape');
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.rows, []);
  assert.equal(typeof r.data.traders, 'number');
  assert.equal(typeof r.data.trades_24h, 'number');
  assert.equal(r.data.virtual, true);
});

test('closes from opted-in handles print newest-first with percent on margin', async () => {
  const token = await newUser();
  const opt = await req('POST', '/api/leaderboard/opt-in', { token, body: { handle: 'tape_runner' } });
  assert.equal(opt.status, 200);
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const o = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 1000, leverage: 5 } });
  assert.equal(o.status, 200);
  const a = await req('GET', '/api/arena/account', { token });
  PRICES = { BTCUSDT: { price: 110, change: 10, volume: 1 } };   // +10% × 5x → +50% on margin
  const c = await req('POST', '/api/arena/close', { token, body: { position_id: a.data.positions[0].id } });
  assert.equal(c.status, 200);
  const t = await req('GET', '/api/arena/tape');
  assert.equal(t.status, 200);
  assert.ok(t.data.rows.length >= 1);
  const row = t.data.rows[0];
  assert.equal(row.handle, 'tape_runner');
  assert.equal(row.symbol, 'BTCUSDT');
  assert.equal(row.direction, 'LONG');
  assert.equal(row.pct, 50);
  assert.equal(row.reason, 'manual');
  assert.ok(row.closed_at);
  assert.ok(t.data.trades_24h >= 1);
});

test('§4: tape rows never carry dollars, balances, or identities', async () => {
  const t = await req('GET', '/api/arena/tape');
  for (const row of t.data.rows) {
    for (const k of ['pnl', 'margin', 'balance', 'equity', 'user_id', 'email', 'entry', 'exit_price']) {
      assert.ok(!(k in row), `tape row must not expose "${k}"`);
    }
  }
});

test('traders without a handle never appear on the tape', async () => {
  const token = await newUser();   // no opt-in
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const o = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'SHORT', margin: 500, leverage: 2 } });
  assert.equal(o.status, 200);
  const a = await req('GET', '/api/arena/account', { token });
  PRICES = { BTCUSDT: { price: 90, change: -10, volume: 1 } };
  const c = await req('POST', '/api/arena/close', { token, body: { position_id: a.data.positions[0].id } });
  assert.equal(c.status, 200);
  const t = await req('GET', '/api/arena/tape');
  assert.ok(t.data.rows.every((r) => r.handle === 'tape_runner'),
    'anonymous closes must stay off the public tape');
  assert.ok(t.data.trades_24h >= 2, 'aggregate counts still include everyone');
});

// ---- Shipped page wiring (source assertions) ----------------------------

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('arena.html mounts the tape, polls it, and animates only fresh prints', () => {
  assert.match(html, /id="tapePanel"/);
  assert.match(html, /id="tapeRows"/);
  assert.match(html, /id="tapePulse"/);
  assert.match(html, /\/api\/arena\/tape/);
  assert.match(html, /setInterval\(loadTape/);
  assert.match(html, /tape-new/);
  // reduced-motion neutralizes the entrance animation
  assert.match(html, /prefers-reduced-motion: reduce\) \{ \.tape-row\.tape-new \{ animation: none/);
  // i18n cache-buster floor (never assert an exact version)
  const m = html.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 15, `i18n version floor (got ${m && m[1]})`);
});

test('tape strings are localized in all six locales', () => {
  for (const key of ['arena.p_tape', 'arena.tape_empty']) {
    const i = i18n.indexOf(`'${key}'`);
    assert.ok(i >= 0, `${key} present`);
    const slice = i18n.slice(i, i18n.indexOf('\n', i));
    for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) {
      assert.ok(slice.includes(loc), `${key} has ${loc}`);
    }
  }
});
