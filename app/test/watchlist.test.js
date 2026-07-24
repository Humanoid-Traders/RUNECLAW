'use strict';
/**
 * Watchlists — star any symbol from the universal modal; a live strip on the
 * dashboard; and the pattern-alert watch extends beyond HELD symbols to
 * WATCHED ones. Private per-user data; symbols normalize like the Arena
 * ticket so a star always matches what the engine calls the pair.
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
const { MAX_WATCH } = require('../routes/watchlist');
const { transitions } = require('../lib/pattern_watch');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/watchlist', require('../routes/watchlist'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

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
      let d = ''; res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let token;

test('watchlist requires auth and starts empty', async () => {
  assert.equal((await req('GET', '/api/watchlist')).status, 401);
  const reg = await req('POST', '/api/auth/register', { body: { email: 'watch@example.com', password: 'longenough1' } });
  token = reg.data.token;
  const r = await req('GET', '/api/watchlist', { token });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data.symbols, []);
});

test('toggle stars, normalizes, and unstars', async () => {
  const on = await req('POST', '/api/watchlist/toggle', { token, body: { symbol: 'sol' } });
  assert.equal(on.status, 200);
  assert.equal(on.data.watching, true);
  assert.equal(on.data.symbol, 'SOLUSDT');           // "sol" → SOLUSDT
  const list = await req('GET', '/api/watchlist', { token });
  assert.deepEqual(list.data.symbols, ['SOLUSDT']);
  const off = await req('POST', '/api/watchlist/toggle', { token, body: { symbol: 'SOL/USDT' } });
  assert.equal(off.data.watching, false);
  assert.deepEqual((await req('GET', '/api/watchlist', { token })).data.symbols, []);
});

test('junk symbols reject; the cap holds', async () => {
  assert.equal((await req('POST', '/api/watchlist/toggle', { token, body: { symbol: '!!' } })).status, 400);
  for (let i = 0; i < MAX_WATCH; i++) {
    const r = await req('POST', '/api/watchlist/toggle', { token, body: { symbol: `AA${i}` } });
    assert.equal(r.status, 200, `star ${i}`);
  }
  const over = await req('POST', '/api/watchlist/toggle', { token, body: { symbol: 'OVER' } });
  assert.equal(over.status, 400);
  assert.match(over.data.error, /full/);
});

test('pattern watch: a watcher gets the event too, holders phrase first', () => {
  const hits = [{ symbol: 'BTCUSDT', chart_patterns: [{ name: 'Bull Flag', confidence: 0.8, signal: 'bullish' }] }];
  // Holder first, watcher of the same symbol second → one event, held form.
  const r1 = transitions(hits, [
    { user_id: 4, symbol: 'BTCUSDT', direction: 'LONG' },
    { user_id: 4, symbol: 'BTCUSDT', direction: null },
  ], new Map(), 1);
  assert.equal(r1.notify.length, 1);
  assert.equal(r1.notify[0].direction, 'LONG');
  // A pure watcher gets the event with direction null.
  const r2 = transitions(hits, [{ user_id: 8, symbol: 'BTCUSDT', direction: null }], new Map(), 1);
  assert.equal(r2.notify.length, 1);
  assert.equal(r2.notify[0].direction, null);
});

test('wiring: watch reads the watchlist; dashboard has star + strip', () => {
  const pw = fs.readFileSync(path.join(__dirname, '..', 'lib', 'pattern_watch.js'), 'utf8');
  assert.match(pw, /FROM user_watchlist/);
  assert.match(pw, /it's on your watchlist/);
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /id="symStar"/);
  assert.match(dash, /\/api\/watchlist\/toggle/);
  assert.match(dash, /function watchStripLoader/);
  assert.match(dash, /id="p-watch"/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  const m = html.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 101, `dashboard.js version floor (got ${m && m[1]})`);
});
