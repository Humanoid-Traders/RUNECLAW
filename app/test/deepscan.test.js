/**
 * Deep Scan web plumbing:
 *  - POST /api/bot/sync/scan preserves the `deepscan` pattern block across a
 *    regular scan that doesn't carry one (so the Deep Scan view keeps the last
 *    readout between deep scans), and a fresh block replaces it.
 *  - GET /api/patterns proxies the bot bridge's /patterns/{symbol} (mocked),
 *    validates the symbol/timeframe, and degrades honestly when the bridge is
 *    unreachable.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const SECRET = process.env.BOT_SYNC_SECRET;

// ── A stub "bot bridge" so /api/patterns has something to proxy ──────────────
let bridge, server, base;
const bridgeReplies = {};

test.after(() => { if (bridge) bridge.close(); if (server) server.close(); });
function req(method, path, { secret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(secret ? { 'x-bot-secret': secret } : {}),
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

test.before(async () => {
  // Bring the stub bridge up and set BOT_API_URL BEFORE requiring the proxy —
  // patterns.js reads BOT_API_URL at module load, so ordering matters.
  bridge = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://x');
    // The proxy sends the symbol %2F-encoded; the real bridge (Starlette)
    // decodes the path param, so match on the decoded path here too.
    const reply = bridgeReplies[decodeURIComponent(url.pathname)];
    if (!reply) { res.writeHead(404).end(JSON.stringify({ detail: 'no data' })); return; }
    res.writeHead(reply.status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(reply.body));
  });
  await new Promise(r => bridge.listen(0, '127.0.0.1', r));
  process.env.BOT_API_URL = `http://127.0.0.1:${bridge.address().port}`;

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/patterns', require('../routes/patterns'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

const DEEPSCAN = {
  hits: [{
    symbol: 'BTC/USDT', price: 65000, chg: -0.3, rsi: 35, vol_spike: false,
    chart_patterns: [{ name: 'Double Top', signal: 'bearish', confidence: 0.74 }],
    candle_patterns: { doji: 'neutral' },
  }],
  count: 1, tf: '4h', generated_at: '2026-07-18 17:19 UTC',
};

test('a deepscan sync stores the block, and a plain scan preserves it', async () => {
  // Deep scan arrives with the pattern block.
  let r = await req('POST', '/api/bot/sync/scan', { secret: SECRET,
    body: { symbols: {}, deepscan: DEEPSCAN } });
  assert.strictEqual(r.status, 200);
  let g = await req('GET', '/api/bot/sync/scan');
  assert.ok(g.data.scan.deepscan, 'deepscan block should be stored');
  assert.strictEqual(g.data.scan.deepscan.hits[0].symbol, 'BTC/USDT');

  // A regular scan (no deepscan) must NOT wipe it.
  r = await req('POST', '/api/bot/sync/scan', { secret: SECRET,
    body: { symbols: { ETHUSDT: { price: 3000 } } } });
  assert.strictEqual(r.status, 200);
  g = await req('GET', '/api/bot/sync/scan');
  assert.ok(g.data.scan.deepscan, 'deepscan must survive a plain scan');
  assert.strictEqual(g.data.scan.deepscan.hits[0].symbol, 'BTC/USDT');
  assert.ok(g.data.scan.symbols.ETHUSDT, 'the plain scan payload still applies');
});

test('a fresh deepscan replaces the previous one', async () => {
  const fresh = { ...DEEPSCAN, hits: [{ ...DEEPSCAN.hits[0], symbol: 'SOL/USDT' }] };
  await req('POST', '/api/bot/sync/scan', { secret: SECRET,
    body: { symbols: {}, deepscan: fresh } });
  const g = await req('GET', '/api/bot/sync/scan');
  assert.strictEqual(g.data.scan.deepscan.hits[0].symbol, 'SOL/USDT');
});

test('GET /api/patterns proxies the bridge and validates input', async () => {
  bridgeReplies['/patterns/BTC/USDT'] = { status: 200, body: {
    symbol: 'BTC/USDT', timeframe: '4h',
    chart_patterns: [{ name: 'Head & Shoulders', signal: 'bearish', confidence: 0.89 }],
    candlestick_patterns: { shooting_star: 'bearish' }, price: 65000,
  } };
  let r = await req('GET', '/api/patterns?symbol=BTC/USDT&timeframe=4h');
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.chart_patterns[0].name, 'Head & Shoulders');
  assert.strictEqual(r.data.candlestick_patterns.shooting_star, 'bearish');

  // Bad symbol / timeframe are rejected before hitting the bridge.
  assert.strictEqual((await req('GET', '/api/patterns?symbol=..%2Fetc')).status, 400);
  assert.strictEqual((await req('GET', '/api/patterns?symbol=BTC/USDT&timeframe=99z')).status, 400);
});

test('GET /api/patterns degrades to 502 when the bridge has no data', async () => {
  const r = await req('GET', '/api/patterns?symbol=DOGE/USDT&timeframe=1h');
  assert.ok(r.status === 502 || r.status === 404, `expected upstream failure, got ${r.status}`);
});
