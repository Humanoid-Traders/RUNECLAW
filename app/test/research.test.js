'use strict';
/**
 * Research dossiers: composed only from trusted sources with each section
 * naming its source, honest handling of unlisted coins and missing data,
 * the authed endpoint, and the chat intercept.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const rwa = require('../lib/rwa');
const dex = require('../lib/dex');
const research = require('../lib/research');

let server, base;

test.before(async () => {
  rwa.setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
    PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
  }));
  dex.setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
  }));
  dex.setMidsFetcher(async () => ({ BTC: '100050' }));
  research.setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
    PENDLEUSDT: { price: 3.5, change: 4, volume: 5e7 },
  }));
  // Keep the safety read's on-chain lookup off the network in tests.
  require('../lib/token_safety').setPairSearcher(async () => null);

  // Seed engine history: signals + closed trades on PENDLE.
  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score,
       pattern, regime, entry_price, stop_loss, take_profit, rr, thesis,
       status, pnl, created_at, resolved_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ['sig-p1', 'PENDLE/USDT', 'LONG', 0.8, 0.8, 'breakout', 'TREND_UP',
     3.4, 3.2, 3.9, 2.5, null, 'NEW', null, new Date().toISOString(), '']);
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
    [1, 'PENDLE/USDT', 'LONG', 3.0, 3.3, 500, 50, 1, null,
     new Date(Date.now() - 86400000), new Date()]);

  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/research', require('../routes/research'));
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

async function newUser() {
  const r = await req('POST', '/api/auth/register', {
    body: { email: `res${Date.now()}@example.com`, password: 'longenough1' },
  });
  return r.data.token;
}

test('buildDossier: PENDLE gets market + RWA + signals + track sections, each sourced', async () => {
  const d = await research.buildDossier('PENDLE');
  assert.ok(d);
  assert.equal(d.read_only, true);
  const titles = d.sections.map(s => s.title);
  assert.ok(titles.includes('Market read'));
  assert.ok(titles.includes('RWA sector'));            // PENDLE is in the DeFi bucket
  assert.ok(titles.includes('Engine signal history'));
  assert.ok(titles.includes('Agent track record here'));
  for (const s of d.sections) assert.ok(s.source, s.title);
  const sig = d.sections.find(s => s.title === 'Engine signal history');
  assert.match(sig.html, /1 recorded signal/);
  assert.match(sig.html, /1 long \/ 0 short/);
  const tr = d.sections.find(s => s.title === 'Agent track record here');
  assert.match(tr.html, /1<\/b> trade/);
  assert.match(tr.html, /\+\$50\.00/);
  assert.ok(d.sources.length >= 3);
  assert.match(d.next_step, /analyze PENDLE/);
  assert.match(d.disclaimer, /no scraped or generated claims/i);
});

test('buildDossier: BTC gets the DEX section; unlisted coin → null', async () => {
  const d = await research.buildDossier('BTC');
  const dexSec = d.sections.find(s => s.title === 'DEX presence');
  assert.ok(dexSec);
  assert.match(dexSec.html, /Hyperliquid/);
  assert.match(dexSec.html, /\+5 bps/);
  // Not listed on the venue → no dossier, ever.
  assert.equal(await research.buildDossier('NOTACOIN'), null);
});

test('REST: authed dossier; unlisted 404; anonymous 401', async () => {
  const token = await newUser();
  const r = await req('GET', '/api/research/pendle', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.base, 'PENDLE');

  const missing = await req('GET', '/api/research/NOTACOIN', { token });
  assert.equal(missing.status, 404);

  const anon = await req('GET', '/api/research/pendle');
  assert.equal(anon.status, 401);
});

test('chat: "research PENDLE" returns the dossier; unlisted honest; other text proxies', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'research PENDLE' } });
  assert.equal(r.data.intent, 'research');
  assert.match(r.data.reply_html, /Research dossier — PENDLE/);
  assert.match(r.data.reply_html, /Market read/);
  assert.match(r.data.reply_html, /Sources:/);
  assert.match(r.data.reply_html, /Not financial advice/);

  const un = await req('POST', '/api/chat', { token, body: { text: 'research NOTACOIN' } });
  assert.equal(un.data.intent, 'research');
  assert.match(un.data.reply_html, /isn't listed on/);

  // "research the market" (no clean symbol) must NOT be intercepted.
  const loose = await req('POST', '/api/chat', { token, body: { text: 'research the market please' } });
  assert.equal(loose.status, 503);   // falls through to the (unconfigured) bot proxy
});
