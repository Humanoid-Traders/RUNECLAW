'use strict';
/**
 * Practice-follow — mirror the engine's signal stream into the PAPER arena
 * account. §4 by construction: paper only (no live-venue path exists in the
 * arena), starts from the NEXT signal (never back-fills a flattering history),
 * fills at the live mark, revocable any time. Planner is pure; the sweep is
 * lazy (account reads), so no background jobs.
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
const { planFollows, validateFollow } = require('../lib/arena_follow');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

// ---- Pure planner -------------------------------------------------------

const MARKS = { BTCUSDT: { price: 100 }, ETHUSDT: { price: 50 } };
const PREFS = { margin: 200, leverage: 2 };

test('planFollows opens valid signals and tracks last_id', () => {
  const plan = planFollows({ signals: [
    { id: 7, symbol: 'BTCUSDT', direction: 'LONG' },
    { id: 9, symbol: 'ETHUSDT', direction: 'SHORT' },
  ], positions: [], balance: 1000, prefs: PREFS, marks: MARKS });
  assert.equal(plan.opens.length, 2);
  assert.equal(plan.opens[0].price, 100);
  assert.equal(plan.last_id, 9);
});

test('planFollows skips duplicates, full slots, thin balance and unknown marks', () => {
  const plan = planFollows({ signals: [
    { id: 1, symbol: 'BTCUSDT', direction: 'LONG' },    // already open
    { id: 2, symbol: 'NOPEUSDT', direction: 'LONG' },   // no mark
    { id: 3, symbol: 'ETHUSDT', direction: 'LONG' },    // opens (uses last 200)
    { id: 4, symbol: 'ETHUSDT', direction: 'SHORT' },   // now duplicate
  ], positions: [{ symbol: 'BTCUSDT' }], balance: 200, prefs: PREFS, marks: MARKS });
  assert.equal(plan.opens.length, 1);
  assert.equal(plan.opens[0].symbol, 'ETHUSDT');
  const reasons = plan.skips.map((s) => s.reason);
  assert.deepEqual(reasons.sort(), ['already_open', 'already_open', 'no_mark'].sort());
  assert.equal(plan.last_id, 4, 'skipped signals still advance the cursor — no re-processing');
});

test('validateFollow enforces margin + leverage when enabling; disable is always ok', () => {
  assert.ok(validateFollow({ enabled: true, margin: 200, leverage: 3 }).ok);
  assert.ok(!validateFollow({ enabled: true, margin: 1, leverage: 3 }).ok);
  assert.ok(!validateFollow({ enabled: true, margin: 200, leverage: 99 }).ok);
  assert.ok(validateFollow({ enabled: false }).ok);
});

// ---- End-to-end through the API ----------------------------------------

let server, base;
let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
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

async function pushSignal(key, symbol, direction) {
  await pool.execute(
    'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    [key, symbol, direction, 0.7, 1, 'test', 'trend', 100, 95, 110, 2, 't', 'NEW', null, new Date(), null]);
}

test('a follower mirrors only signals emitted AFTER enabling, at the live mark', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'follow1@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  await pushSignal('old-1', 'BTCUSDT', 'LONG');                 // pre-follow: must NOT open
  const on = await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } });
  assert.equal(on.status, 200);
  let a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0, 'no back-fill of pre-follow signals');
  assert.equal(a.data.follow.enabled, true);
  // a NEW signal arrives → next account read opens it at the live mark
  PRICES = { BTCUSDT: { price: 120, change: 0, volume: 1 } };
  await pushSignal('new-1', 'BTCUSDT', 'LONG');
  a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 1);
  assert.equal(a.data.positions[0].source, 'signal');
  assert.equal(a.data.positions[0].entry, 120, 'filled at the LIVE mark, not the stale signal price');
  assert.equal(a.data.balance, 9800);
  // disabling stops the mirror
  await req('POST', '/api/arena/follow', { token, body: { enabled: false } });
  await pushSignal('new-2', 'ETHUSDT', 'LONG');
  PRICES = { BTCUSDT: { price: 120, change: 0, volume: 1 }, ETHUSDT: { price: 50, change: 0, volume: 1 } };
  a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 1, 'no opens while disabled');
});

test('the /arena page mounts the practice-follow panel + signal chips', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="followPanel"/);
  assert.match(html, /api\/arena\/follow/);
  assert.match(html, /paper only/i);
  assert.match(html, /never back-fills/i);
  assert.match(html, /⚡ signal/);
});
