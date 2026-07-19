'use strict';
/**
 * Funds by venue & wallet (web side): the gateway per-venue fan-out and the
 * SIWE wallet's per-chain breakdown combine into an itemised view; the real
 * total counts only readable real money; an unreadable venue shows as an error
 * row (never a fabricated $0) and flips the `partial` flag; auth is enforced.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB3_CHAINS = 'ethereum';   // single-chain FakeProvider
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(40);   // set BEFORE requiring routes
const GW_PORT = 39878;
process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${GW_PORT}`;
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

// Fake bot gateway (the /gateway/holdings upstream): two connected venues,
// one readable and one erroring.
let gwServer;
let gwResponse = {
  read_only: true,
  venues: [
    { venue: 'bybit', active: true, ok: true, currency: 'USDT',
      equity_usd: 512.34, detail: '512.34 USDT total' },
    { venue: 'okx', active: false, ok: false, equity_usd: null,
      detail: 'venue timeout' },
  ],
  venue_total_usd: 512.34, venue_count: 2,
};
let gwStatus = 200;

test.before(async () => {
  gwServer = http.createServer((req, res) => {
    res.setHeader('Content-Type', 'application/json');
    res.statusCode = gwStatus;
    res.end(JSON.stringify(gwResponse));
  });
  await new Promise((r) => gwServer.listen(GW_PORT, '127.0.0.1', r));
});

const authModule = require('../auth');
const { pool } = require('../db');
const wallet = require('../lib/wallet');

// Fake chain: 1 ETH @ $2,500 + 500 USDC = $3,000.
const USDC_ADDR = wallet.TOKENS.find(t => t.symbol === 'USDC').address;
class FakeProvider {
  async getBalance() { return 10n ** 18n; }
  async call(tx) {
    const raw = String(tx.to).toLowerCase() === USDC_ADDR.toLowerCase()
      ? 500n * 10n ** 6n : 0n;
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  async getNetwork() { return { chainId: 1n }; }
  async resolveName(n) { return n; }
}

let server, base;

test.before(async () => {
  wallet.setProviderFactory(() => new FakeProvider());
  wallet.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 0, volume: 1 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/holdings', require('../routes/holdings'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); if (gwServer) gwServer.close(); });

function req(method, path, { token } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${path}`, {
      method,
      headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

let seq = 0;

// register needs a JSON body; do it directly.
function register(email) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify({ email, password: 'longenough1' });
    const r = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); r.write(payload); r.end();
  });
}

const ADDR = '0x' + 'ef'.repeat(20);
async function linkWallet(token) {
  const me = await req('GET', '/api/auth/me', { token });
  const uid = me.data.id ?? me.data.user_id;
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, uid]);
}

test('holdings: itemised venues + wallet chains; real total counts readable only', async () => {
  seq++;
  const reg = await register(`hd${seq}@example.com`);
  assert.equal(reg.status, 200);
  const token = reg.data.token;
  await linkWallet(token);

  const r = await req('GET', '/api/holdings', { token });
  assert.equal(r.status, 200);
  const d = r.data;
  assert.equal(d.read_only, true);
  // Two venue rows, one readable one not.
  assert.equal(d.venues.length, 2);
  const bybit = d.venues.find(v => v.venue === 'bybit');
  const okx = d.venues.find(v => v.venue === 'okx');
  assert.equal(bybit.ok, true);
  assert.equal(bybit.equity_usd, 512.34);
  assert.equal(okx.ok, false);
  assert.equal(okx.equity_usd, null);            // never a fabricated zero
  assert.match(okx.detail, /timeout/);
  // Wallet: one chain (ethereum) with $3,000.
  assert.equal(d.wallet.linked, true);
  assert.equal(d.wallet.chains.length, 1);
  assert.equal(d.wallet.chains[0].total_usd, 3000);
  // Real total = 512.34 (bybit) + 3000 (wallet); okx unreadable → excluded.
  assert.equal(d.total_real_usd, 3512.34);
  assert.equal(d.sources_counted, 2);
  assert.equal(d.partial, true);                 // okx unreadable flips it
});

test('holdings: gateway down + unlinked wallet still answers; anon is 401', async () => {
  seq++;
  const reg = await register(`hd${seq}@example.com`);
  const token = reg.data.token;                   // no wallet link
  gwStatus = 503;
  const r = await req('GET', '/api/holdings', { token });
  gwStatus = 200;
  assert.equal(r.status, 200);
  assert.equal(r.data.venues_available, false);
  assert.equal(r.data.venues.length, 0);
  assert.equal(r.data.wallet.linked, false);
  assert.equal(r.data.total_real_usd, null);      // nothing real readable
  const anon = await req('GET', '/api/holdings');
  assert.equal(anon.status, 401);
});
