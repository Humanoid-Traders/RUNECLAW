'use strict';
/**
 * Cross-venue exposure intelligence: pure netting math (perp vs spot,
 * wrapped-asset mapping, stables-as-cash), risk-desk flags (stacked longs,
 * hedges, concentration), the authed endpoint over seeded positions +
 * wallet, and the chat intercept.
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
const wallet = require('../lib/wallet');
const exposure = require('../lib/exposure');

let server, base;

// Fake chain: 1 ETH + 200 ONDO + 500 USDC.
const ONDO_ADDR = wallet.TOKENS.find(t => t.symbol === 'ONDO').address;
const USDC_ADDR = wallet.TOKENS.find(t => t.symbol === 'USDC').address;
class FakeProvider {
  async getBalance() { return 10n ** 18n; }
  async call(tx) {
    const to = String(tx.to).toLowerCase();
    let raw = 0n;
    if (to === ONDO_ADDR.toLowerCase()) raw = 200n * 10n ** 18n;
    if (to === USDC_ADDR.toLowerCase()) raw = 500n * 10n ** 6n;
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  async getNetwork() { return { chainId: 1n }; }
  async resolveName(n) { return n; }
}

test.before(async () => {
  wallet.setProviderFactory(() => new FakeProvider());
  wallet.setTickerFetcher(async () => ({
    ETHUSDT: { price: 2500, change: 0, volume: 1 },
    ONDOUSDT: { price: 1, change: 0, volume: 1 },
  }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/exposure', require('../routes/exposure'));
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

let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `exp${seq}@example.com`, password: 'longenough1' },
  });
  return r.data.token;
}

async function uidOf(token) {
  const me = await req('GET', '/api/auth/me', { token });
  return me.data.id ?? me.data.user_id;
}

// ── Pure math ────────────────────────────────────────────────────────────────

test('computeExposure: nets perps vs spot with wrapped mapping + stables as cash', () => {
  const e = exposure.computeExposure(
    [
      { symbol: 'ETH/USDT', direction: 'LONG', size_usd: 1000 },
      { symbol: 'BTC/USDT', direction: 'SHORT', size_usd: 800 },
    ],
    [
      { symbol: 'WETH', usd: 500 },       // maps to ETH spot
      { symbol: 'WBTC', usd: 400 },       // maps to BTC spot (hedged vs short)
      { symbol: 'USDC', usd: 900 },       // cash, not exposure
    ]);
  const by = Object.fromEntries(e.assets.map(a => [a.base, a]));
  assert.equal(by.ETH.perp_long_usd, 1000);
  assert.equal(by.ETH.spot_usd, 500);
  assert.equal(by.ETH.net_usd, 1500);
  assert.ok(by.ETH.flags.includes('stacked_long'));
  assert.equal(by.BTC.perp_short_usd, 800);
  assert.equal(by.BTC.spot_usd, 400);
  assert.equal(by.BTC.net_usd, -400);
  assert.ok(by.BTC.flags.includes('hedged'));
  assert.ok(!by.BTC.flags.includes('stacked_long'));
  assert.equal(e.cash_usd, 900);
  assert.equal(e.gross_total_usd, 1000 + 500 + 800 + 400);
  assert.equal(e.net_total_usd, 1500 - 400);
  // One warning: the ETH stacked long (2700/2700 ETH is 55% → also concentrated).
  assert.ok(e.warnings.some(w => /ETH.*same bet twice/.test(w)));
  assert.equal(e.read_only, true);
});

test('computeExposure: concentration flag over half of gross', () => {
  const e = exposure.computeExposure(
    [
      { symbol: 'SOL/USDT', direction: 'LONG', size_usd: 9000 },
      { symbol: 'DOGE/USDT', direction: 'LONG', size_usd: 1000 },
    ], []);
  const sol = e.assets.find(a => a.base === 'SOL');
  assert.ok(sol.flags.includes('concentrated'));
  assert.ok(e.warnings.some(w => /SOL is 90%/.test(w)));
  // A single-asset book is trivially 100% — no concentration noise then.
  const single = exposure.computeExposure(
    [{ symbol: 'SOL/USDT', direction: 'LONG', size_usd: 9000 }], []);
  assert.ok(!single.assets[0].flags.includes('concentrated'));
});

test('computeExposure: empty inputs → clean empty, malformed rows skipped', () => {
  const e = exposure.computeExposure(
    [{ symbol: 'ETH/USDT', direction: 'LONG', size_usd: 'garbage' }], null);
  assert.equal(e.assets.length, 0);
  assert.equal(e.gross_total_usd, 0);
  assert.equal(e.warnings.length, 0);
});

// ── Endpoint + chat ──────────────────────────────────────────────────────────

test('REST: exposure over seeded open positions + linked wallet', async () => {
  const token = await newUser();
  const uid = await uidOf(token);
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees,
       status, pattern, stop_loss, take_profit)
     VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)`,
    [uid, 'ETH/USDT', 'LONG', 2500, 1000, 1, null, 2400, 2700]);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
    ['0x' + 'ef'.repeat(20), uid]);

  const r = await req('GET', '/api/exposure', { token });
  assert.equal(r.status, 200);
  const eth = r.data.assets.find(a => a.base === 'ETH');
  // 1000 perp long + 2500 spot ETH (1 ETH @ 2500) = stacked long.
  assert.equal(eth.perp_long_usd, 1000);
  assert.equal(eth.spot_usd, 2500);
  assert.ok(eth.flags.includes('stacked_long'));
  assert.equal(r.data.wallet_included, true);
  assert.equal(r.data.cash_usd, 500);   // the fake wallet's USDC

  const anon = await req('GET', '/api/exposure');
  assert.equal(anon.status, 401);
});

test('chat: "what\'s my total exposure?" answers with flags; empty book honest', async () => {
  const token = await newUser();
  const empty = await req('POST', '/api/chat', {
    token, body: { text: "what's my total exposure?" } });
  assert.equal(empty.data.intent, 'exposure');
  assert.match(empty.data.reply_html, /No directional exposure/);

  const uid = await uidOf(token);
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees,
       status, pattern, stop_loss, take_profit)
     VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)`,
    [uid, 'SOL/USDT', 'SHORT', 150, 600, 1, null, 160, 130]);
  const r = await req('POST', '/api/chat', { token, body: { text: 'am I overexposed?' } });
  assert.equal(r.data.intent, 'exposure');
  assert.match(r.data.reply_html, /SOL/);
  assert.match(r.data.reply_html, /short \$600/);
  assert.match(r.data.reply_html, /nothing here can resize/i);

  const other = await req('POST', '/api/chat', { token, body: { text: 'hello!' } });
  assert.equal(other.status, 503);   // unconfigured bot proxy
});
