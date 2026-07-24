'use strict';
/**
 * Take-profit / stop-loss on Arena positions — automatic exits that teach
 * real discipline. exitCheck priority: liquidation (the exchange always
 * wins) → stop-loss → take-profit; TP/SL closes credit margin + pnl at the
 * TRIGGER price, liquidations still forfeit the margin. Practice-follow
 * inherits each signal's own stop/target when valid against the live fill.
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
const arena = require('../lib/arena');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`);

test('validateTpSl enforces sides per direction and rejects junk', () => {
  assert.ok(arena.validateTpSl('LONG', 100, 110, 95).ok);
  assert.ok(!arena.validateTpSl('LONG', 100, 90, null).ok, 'long tp below entry');
  assert.ok(!arena.validateTpSl('LONG', 100, null, 105).ok, 'long sl above entry');
  assert.ok(arena.validateTpSl('SHORT', 100, 90, 105).ok);
  assert.ok(!arena.validateTpSl('SHORT', 100, 110, null).ok);
  assert.ok(!arena.validateTpSl('LONG', 100, -5, null).ok);
  const none = arena.validateTpSl('LONG', 100, null, '');
  assert.ok(none.ok && none.data.tp === null && none.data.sl === null);
});

test('exitCheck priority: liquidation beats sl beats tp; trigger prices honored', () => {
  const pos = { direction: 'LONG', entry: 100, margin: 100, leverage: 10, tp: 110, sl: 95 };
  assert.equal(arena.exitCheck(pos, 100), null);
  assert.deepEqual(arena.exitCheck(pos, 111), { reason: 'tp', price: 110 });
  assert.deepEqual(arena.exitCheck(pos, 94), { reason: 'sl', price: 95 });
  const crash = arena.exitCheck(pos, 80);              // past liq (~90)
  assert.equal(crash.reason, 'liquidated');
  const short = { direction: 'SHORT', entry: 100, margin: 100, leverage: 5, tp: 90, sl: 106 };
  assert.deepEqual(arena.exitCheck(short, 89), { reason: 'tp', price: 90 });
  assert.deepEqual(arena.exitCheck(short, 107), { reason: 'sl', price: 106 });
});

// ---- End-to-end ---------------------------------------------------------
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

test('a take-profit fills at the trigger and credits margin + pnl', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'tpsl1@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const bad = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 1000, leverage: 5, tp: 90 } });
  assert.equal(bad.status, 400, 'tp below entry rejected');
  const o = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 1000, leverage: 5, tp: 110, sl: 95 } });
  assert.equal(o.status, 200);
  assert.equal(o.data.filled.tp, 110);
  // Price gaps THROUGH the target → close at the TRIGGER (110), not the gap
  PRICES = { BTCUSDT: { price: 118, change: 18, volume: 1 } };
  const a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0);
  assert.equal(a.data.history[0].reason, 'tp');
  assert.equal(a.data.history[0].exit_price, 110);
  near(a.data.history[0].pnl, 500, 0.01);              // +10% × 5 on 1000
  near(a.data.balance, 10500, 0.01);
});

test('a stop-loss caps the damage automatically', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'tpsl2@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  PRICES = { ETHUSDT: { price: 100, change: 0, volume: 1 } };
  await req('POST', '/api/arena/open', { token, body: { symbol: 'ETHUSDT', direction: 'LONG', margin: 1000, leverage: 2, sl: 96 } });
  PRICES = { ETHUSDT: { price: 92, change: -8, volume: 1 } };
  const a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.history[0].reason, 'sl');
  assert.equal(a.data.history[0].exit_price, 96);
  near(a.data.history[0].pnl, -80, 0.01);              // -4% × 2 on 1000
  near(a.data.balance, 9920, 0.01);
});

test('practice-follow inherits the signal\'s own stop/target when valid', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'tpsl3@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  PRICES = { SOLUSDT: { price: 100, change: 0, volume: 1 } };
  await req('POST', '/api/arena/follow', { token, body: { enabled: true, margin: 200, leverage: 2 } });
  await pool.execute(
    'INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern, regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
    ['tpsl-sig-1', 'SOLUSDT', 'LONG', 0.7, 1, 't', 'trend', 99, 94, 112, 2, 't', 'NEW', null, new Date(), null]);
  const a = await req('GET', '/api/arena/account', { token });
  const p = a.data.positions.find((x) => x.symbol === 'SOLUSDT');
  assert.ok(p, 'signal opened');
  assert.equal(p.tp, 112);
  assert.equal(p.sl, 94);
});

test('the ticket + table ship the TP/SL surface', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="tTp"/);
  assert.match(html, /id="tSl"/);
  assert.match(html, /TP \/ SL/);
});
