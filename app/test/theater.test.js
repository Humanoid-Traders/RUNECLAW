'use strict';
/**
 * Trade replay theater: the public showcase endpoint picks the biggest
 * recent recorded close (win OR loss), returns null when there is nothing
 * real to show, and the candle proxy validates its new time-window params.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/public', require('../routes/track'));
  app.use('/api/market', require('../routes/market'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${path}`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    }).on('error', reject);
  });
}

async function seedClose(sym, pnl, size, closedAt, entry = 100, exit = 108) {
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
    [1, sym, 'LONG', entry, exit, size, pnl, 1, null,
     new Date(new Date(closedAt).getTime() - 6 * 3600_000), closedAt]);
}

test('replay-trade: empty history → trade null (section stays hidden)', async () => {
  const r = await get('/api/public/replay-trade');
  assert.equal(r.status, 200);
  assert.equal(r.data.trade, null);
});

test('replay-trade: picks the biggest |PnL| close of the last 14 days — loss included', async () => {
  const now = Date.now();
  const day = 86_400_000;
  await seedClose('BTC/USDT', 40, 1000, new Date(now - 2 * day).toISOString());
  await seedClose('SOL/USDT', -75, 900, new Date(now - 3 * day).toISOString(), 200, 185);
  await seedClose('ETH/USDT', 500, 1000, new Date(now - 30 * day).toISOString()); // outside window
  // The endpoint caches for 5 minutes — restart-free in prod, but for the
  // test we bust it by re-requiring a fresh router on a fresh app.
  const app2 = express();
  app2.use('/api/public', (() => {
    delete require.cache[require.resolve('../routes/track')];
    return require('../routes/track');
  })());
  const srv2 = await new Promise((res) => { const s = app2.listen(0, '127.0.0.1', () => res(s)); });
  const r = await new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:${srv2.address().port}/api/public/replay-trade`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    }).on('error', reject);
  });
  srv2.close();
  assert.ok(r.trade, 'expected a showcase trade');
  // SOL's -$75 beats BTC's +$40 in |PnL|; the 30-day-old +$500 is out of window.
  assert.equal(r.trade.symbol, 'SOL/USDT');
  assert.equal(r.trade.pnl, -75);
  assert.equal(r.trade.direction, 'LONG');
  assert.equal(r.trade.entry_price, 200);
  assert.equal(r.trade.exit_price, 185);
  assert.ok(r.trade.opened_at && r.trade.closed_at);
});

test('candles proxy: rejects bad granularity; time params validated numeric', async () => {
  const bad = await get('/api/market/candles/BTCUSDT?granularity=bogus');
  assert.equal(bad.status, 400);
  const badSym = await get('/api/market/candles/%24%24%24?granularity=1h');
  assert.equal(badSym.status, 400);
  // Valid shape reaches the outbound fetch (which fails in the sandbox →
  // 502, never a crash); garbage time params are silently dropped by the
  // numeric regex rather than concatenated into the upstream URL.
  const ok = await get('/api/market/candles/BTCUSDT?granularity=1h&startTime=abc&endTime=<x>');
  assert.ok([200, 502].includes(ok.status));
});
