'use strict';
/**
 * Market proxy routes vs Bitget's CURRENT API contract (drift of 2026-07,
 * verified against the live API):
 *  - candles: hour+ granularities must be UPPERCASE (1H/4H/1D) and minutes
 *    short (15m) — lowercase "1h" is rejected with code 400171 inside an
 *    HTTP 200, which the chart used to render as a false "no candle data";
 *  - merge-depth: precision=price now returns 40020 — the param is omitted;
 *  - Bitget errors (code != 00000 in an HTTP 200) must surface as 502, not
 *    as an empty-state lie.
 * https.get is stubbed to capture the upstream URL and play canned bodies.
 * Also pins the /api/insight?symbol= query form (hosting proxies 404 the
 * %2F an encoded-slash PATH segment needs, so the client now uses a query).
 */
process.env.JWT_SECRET = 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const { EventEmitter } = require('node:events');
const https = require('node:https');

// ── Stub https.get BEFORE the router is required ──────────────────────────
let lastUrl = null;
let nextBody = { code: '00000', data: [] };
https.get = (url, _opts, cb) => {
  lastUrl = String(url);
  const res = new EventEmitter();
  const req = new EventEmitter();
  setImmediate(() => {
    cb(res);
    res.emit('data', JSON.stringify(nextBody));
    res.emit('end');
  });
  req.destroy = () => {};
  return req;
};

const express = require('express');
let server, base;

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${path}`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, body: d }));
    }).on('error', reject);
  });
}

test.before(async () => {
  const app = express();
  app.use('/api/market', require('../routes/market'));
  app.use('/api/insight', require('../routes/insight'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('candles translate UI granularities to Bitget current tokens', async () => {
  for (const [ui, bitget] of [['1h', '1H'], ['4h', '4H'], ['1d', '1D'], ['15min', '15m']]) {
    nextBody = { code: '00000', data: [['1', '2', '3', '4', '5']] };
    const r = await get(`/api/market/candles/BTCUSDT?granularity=${ui}&limit=5`);
    assert.strictEqual(r.status, 200, `${ui} should proxy fine`);
    assert.ok(lastUrl.includes(`granularity=${bitget}`),
      `${ui} must reach Bitget as ${bitget} (got ${lastUrl})`);
  }
});

test('depth no longer sends the rejected precision=price param', async () => {
  nextBody = { code: '00000', data: { asks: [], bids: [] } };
  const r = await get('/api/market/depth/BTCUSDT');
  assert.strictEqual(r.status, 200);
  assert.ok(!lastUrl.includes('precision='), `precision must be omitted (got ${lastUrl})`);
});

test('a Bitget error inside HTTP 200 surfaces as 502, never a fake empty state', async () => {
  nextBody = { code: '400171', msg: 'Parameter verification failed' };
  const r = await get('/api/market/candles/ETHUSDT?granularity=1h&limit=5');
  assert.strictEqual(r.status, 502);
  assert.ok(r.body.includes('400171') || r.body.includes('Parameter'));
});

test('insight accepts the proxy-safe ?symbol= query form', async () => {
  // Invalid symbol proves the query route matched and validated (a 404 here
  // would mean the route shape regressed to path-only).
  const r = await get('/api/insight?symbol=%24bad%24');
  assert.strictEqual(r.status, 400);
  assert.ok(r.body.includes('Invalid symbol'));
});
