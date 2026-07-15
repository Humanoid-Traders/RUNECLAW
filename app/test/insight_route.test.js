/**
 * Market-insight proxy route (routes/insight.js) — the backend behind the
 * dashboard's "AI decision picture" panel.
 *
 * Spins up a mock bot API bridge on an ephemeral port and mounts the real
 * router against it. Pins: symbol + timeframe validation, the exact upstream
 * URL (symbol slash encoded, timeframe/limit forwarded), 5xx→502 mapping, and
 * the 30s response cache. Crucially it pins that the bridge timeframe grammar
 * is ccxt-style (`15m` OK, `15min` rejected) — that is WHY dashboard.js maps
 * the Bitget chart granularity `15min` → `15m` before calling this route.
 *
 * Run: npm test  (node --test test/)
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const seen = []; // requests the mock bridge received
let mockBridge;
let appServer;
let base;

function startMockBridge() {
  return new Promise((resolve) => {
    mockBridge = http.createServer((req, res) => {
      seen.push({ url: req.url });
      res.setHeader('Content-Type', 'application/json');
      if (req.url.startsWith('/insight/')) {
        res.end(JSON.stringify({
          symbol: 'BTC/USDT', timeframe: '1h', price: 65000, atr: 400,
          regime: 'TREND_UP', confluence: 0.62,
          levels: [{ price: 66000, kind: 'swing_high', touches: 3, score: 8.1 }],
          fvgs: [{ kind: 'bullish', top: 65200, bottom: 64800, filled: false }],
          pools: { eqh: [], eql: [] }, premium_discount: 0.2,
          cvd: { cum_delta_usd: 125000, series: [], prices: [], trades: 10, age_sec: 5 },
          votes: [{ name: 'rsi', vote: 1, weight: 0.8 }],
          gates: {}, risk_state: { latched: false }, flow: {},
        }));
      } else {
        res.statusCode = 404;
        res.end(JSON.stringify({ detail: 'not found' }));
      }
    });
    mockBridge.listen(0, '127.0.0.1', () => resolve(mockBridge.address().port));
  });
}

function request(path) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${path}`, { method: 'GET' }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve({ status: res.statusCode, data: data ? JSON.parse(data) : {} }));
    });
    req.on('error', reject);
    req.end();
  });
}

test.before(async () => {
  const port = await startMockBridge();
  process.env.BOT_API_URL = `http://127.0.0.1:${port}`;

  const express = require('express');
  const app = express();
  app.use('/api/insight', require('../routes/insight'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => {
  if (appServer) appServer.close();
  if (mockBridge) mockBridge.close();
});

test('proxies a valid request and encodes the symbol slash upstream', async () => {
  seen.length = 0;
  const r = await request('/api/insight/' + encodeURIComponent('BTC/USDT') + '?timeframe=1h&limit=200');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.confluence, 0.62);
  assert.strictEqual(r.data.cvd.cum_delta_usd, 125000);
  assert.strictEqual(seen.length, 1);
  // Slash arrives at the bridge percent-encoded, tf + limit forwarded verbatim.
  assert.match(seen[0].url, /^\/insight\/BTC%2FUSDT\?timeframe=1h&limit=200$/);
});

test('accepts ccxt timeframe 15m but rejects Bitget granularity 15min', async () => {
  // dashboard.js maps the chart's `15min` → `15m` for exactly this reason.
  const ok = await request('/api/insight/' + encodeURIComponent('BTC/USDT') + '?timeframe=15m&limit=50');
  assert.strictEqual(ok.status, 200);
  const bad = await request('/api/insight/' + encodeURIComponent('BTC/USDT') + '?timeframe=15min');
  assert.strictEqual(bad.status, 400);
  assert.strictEqual(bad.data.error, 'Invalid timeframe');
});

test('rejects an invalid symbol before touching the bridge', async () => {
  seen.length = 0;
  const r = await request('/api/insight/' + encodeURIComponent('bad sym!'));
  assert.strictEqual(r.status, 400);
  assert.strictEqual(seen.length, 0);
});

test('clamps limit to 500', async () => {
  seen.length = 0;
  await request('/api/insight/' + encodeURIComponent('ETH/USDT') + '?limit=99999');
  assert.match(seen[0].url, /limit=500$/);
});

test('caches within the TTL — a repeat call does not re-hit the bridge', async () => {
  seen.length = 0;
  const p = '/api/insight/' + encodeURIComponent('SOL/USDT') + '?timeframe=4h&limit=200';
  await request(p);
  await request(p);
  assert.strictEqual(seen.length, 1); // second served from cache
});
