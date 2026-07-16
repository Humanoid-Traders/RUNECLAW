'use strict';
/**
 * RWA & on-chain radar: curated universe filtered to live listings,
 * volume-weighted sector math, the public endpoint, the chat intercept —
 * and the read-only guarantee stamped on every response.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const rwa = require('../lib/rwa');

let server, base;

const FAKE_TICKERS = {
  BTCUSDT: { price: 98_000, change: 1.0, volume: 5e9 },
  // platforms: ONDO listed, POLYX listed, rest of category unlisted
  ONDOUSDT: { price: 0.9, change: 5.0, volume: 30e6 },
  POLYXUSDT: { price: 0.2, change: -3.0, volume: 10e6 },
  // chains
  ETHUSDT: { price: 2500, change: 2.0, volume: 2e9 },
  XRPUSDT: { price: 2.1, change: -1.0, volume: 1e9 },
  // defi
  PENDLEUSDT: { price: 3.5, change: 4.0, volume: 50e6 },
};

test.before(async () => {
  rwa.setTickerFetcher(async () => FAKE_TICKERS);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/market', require('../routes/market'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

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

// ── Pure radar math ──────────────────────────────────────────────────────────

test('buildRadar: unlisted symbols are omitted, never guessed', () => {
  const r = rwa.buildRadar(FAKE_TICKERS);
  const platforms = r.categories.find(c => c.key === 'platforms');
  assert.equal(platforms.listed, 2);                       // ONDO + POLYX only
  assert.ok(platforms.tracked > platforms.listed);
  assert.deepEqual(platforms.tokens.map(t => t.base), ['ONDO', 'POLYX']); // sorted by change
  const chains = r.categories.find(c => c.key === 'chains');
  assert.equal(chains.listed, 2);                          // ETH + XRP
  assert.equal(r.sector.listed, 5);
});

test('buildRadar: volume-weighted category and sector change', () => {
  const r = rwa.buildRadar(FAKE_TICKERS);
  const platforms = r.categories.find(c => c.key === 'platforms');
  // (5.0*30e6 + -3.0*10e6) / 40e6 = 3.0
  assert.equal(platforms.change_24h_pct, 3);
  // Sector: (5*30e6 - 3*10e6 + 2*2e9 - 1*1e9 + 4*50e6) / (30+10+2000+1000+50)e6
  const expected = (5 * 30 + -3 * 10 + 2 * 2000 + -1 * 1000 + 4 * 50) / (30 + 10 + 2000 + 1000 + 50);
  assert.equal(r.sector.change_24h_pct, Math.round(expected * 100) / 100);
  // vs BTC: sector - 1.0
  assert.equal(r.sector.vs_btc_pct, Math.round((expected - 1) * 100) / 100);
  assert.equal(r.sector.top_gainer.base, 'ONDO');
  assert.equal(r.sector.top_loser.base, 'POLYX');
});

test('buildRadar: read-only stamp + empty tickers → honest empty', () => {
  const r = rwa.buildRadar(FAKE_TICKERS);
  assert.equal(r.read_only, true);
  const empty = rwa.buildRadar({});
  assert.equal(empty.sector.listed, 0);
  assert.equal(empty.sector.change_24h_pct, null);
  assert.equal(empty.btc_change_24h_pct, null);
});

// ── Endpoint + chat ──────────────────────────────────────────────────────────

test('GET /api/market/rwa is public and carries the radar', async () => {
  const r = await req('GET', '/api/market/rwa');
  assert.equal(r.status, 200);
  assert.equal(r.data.read_only, true);
  assert.equal(r.data.sector.listed, 5);
  assert.match(r.data.source, /public/);
});

test('chat: "rwa radar" answers with the sector read; unrelated text proxies', async () => {
  const reg = await req('POST', '/api/auth/register', {
    body: { email: 'rwa1@example.com', password: 'longenough1' },
  });
  const token = reg.data.token;
  const r = await req('POST', '/api/chat', { token, body: { text: 'show me the RWA radar' } });
  assert.equal(r.status, 200);
  assert.equal(r.data.intent, 'rwa');
  assert.match(r.data.reply_html, /RWA radar/);
  assert.match(r.data.reply_html, /ONDO \+5%/);
  assert.match(r.data.reply_html, /never trades/i);

  const other = await req('POST', '/api/chat', { token, body: { text: 'how is bitcoin?' } });
  assert.equal(other.status, 503);   // unconfigured bot proxy
});
