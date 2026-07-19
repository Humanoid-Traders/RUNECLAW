'use strict';
/**
 * Idle-Asset Yield Optimizer (web side): the wallet's priced idle assets are
 * aggregated into holdings and POSTed to the bot optimizer; the response is
 * surfaced verbatim; unlinked wallet / gateway-down fail soft; anon is 401.
 * The optimizer itself is Python (tested there) — this covers the web glue.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB3_CHAINS = 'ethereum';
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(40);
const GW_PORT = 39879;
process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${GW_PORT}`;
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

// Fake bot gateway: echoes a Lido-wins optimizer report for ETH.
let gwServer;
let gwBody = null;
let gwStatus = 200;
let gwResponse = {
  read_only: true,
  recommendations: [{
    asset: 'ETH', idle_usd: 3000, status: 'recommended',
    best: { source: 'Lido', apy: 3.1, custodial: false, lockup_days: 0 },
    est_year_usd: 93.0,
    note: 'non-custodial pick at 3.1% chosen over a custodial 3.5% (Bitget) — you keep custody',
  }],
  unmatched: [], total_idle_usd: 3000, total_deployable_usd: 3000,
  total_est_year_usd: 93.0, sources: { noncustodial: 3 },
};

test.before(async () => {
  gwServer = http.createServer((req, res) => {
    let d = ''; req.on('data', c => d += c);
    req.on('end', () => {
      try { gwBody = JSON.parse(d || '{}'); } catch (e) { gwBody = null; }
      res.setHeader('Content-Type', 'application/json');
      res.statusCode = gwStatus;
      res.end(JSON.stringify(gwResponse));
    });
  });
  await new Promise((r) => gwServer.listen(GW_PORT, '127.0.0.1', r));
});

const authModule = require('../auth');
const { pool } = require('../db');
const wallet = require('../lib/wallet');
const idle = require('../lib/idle_yield');

// Fake chain: 1 ETH @ $2,500 + 500 USDC = $3,000 idle.
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
  app.use('/api/idleyield', require('../routes/idleyield'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); if (gwServer) gwServer.close(); });

function req(method, path, { token } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${path}`, {
      method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject); r.end();
  });
}

let seq = 0;
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
const ADDR = '0x' + 'ab'.repeat(20);
async function linkWallet(token) {
  const me = await req('GET', '/api/auth/me', { token });
  const uid = me.data.id ?? me.data.user_id;
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, uid]);
}

// ── pure helper: wallet → holdings aggregation ────────────────────────

test('holdingsFromWallet aggregates priced assets by symbol, skips unpriced', () => {
  const h = idle.holdingsFromWallet({ assets: [
    { symbol: 'ETH', usd: 2500 }, { symbol: 'USDC', usd: 500 },
    { symbol: 'ETH', usd: 100 },                    // second ETH row sums
    { symbol: 'PEPE', usd: null },                  // unpriced → skipped
  ]});
  const eth = h.find(x => x.asset === 'ETH');
  assert.equal(eth.usd_value, 2600);
  assert.equal(h.find(x => x.asset === 'USDC').usd_value, 500);
  assert.ok(!h.find(x => x.asset === 'PEPE'));
});

// ── route: wallet idle → optimizer report ─────────────────────────────

test('idleyield: linked wallet → best rate report from the optimizer', async () => {
  seq++;
  const reg = await register(`iy${seq}@example.com`);
  assert.equal(reg.status, 200);
  const token = reg.data.token;
  await linkWallet(token);

  const r = await req('GET', '/api/idleyield', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.wallet_linked, true);
  // gateway saw aggregated holdings (ETH $2500 + USDC $500).
  assert.ok(Array.isArray(gwBody.holdings));
  assert.ok(gwBody.holdings.some(h => h.asset === 'ETH'));
  assert.equal(gwBody.prefer_noncustodial, true);
  // report surfaced verbatim.
  const rec = r.data.recommendations[0];
  assert.equal(rec.best.source, 'Lido');
  assert.equal(rec.best.custodial, false);
  assert.equal(r.data.total_est_year_usd, 93.0);
});

test('idleyield: unlinked wallet is honest; gateway-down fails soft; anon 401', async () => {
  seq++;
  const reg = await register(`iy${seq}@example.com`);
  const token = reg.data.token;                     // no wallet link
  const r = await req('GET', '/api/idleyield', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.wallet_linked, false);
  assert.equal(r.data.recommendations.length, 0);

  // gateway 503 with a linked wallet → available:false, not a crash.
  await linkWallet(token);
  gwStatus = 503;
  const r2 = await req('GET', '/api/idleyield', { token });
  gwStatus = 200;
  assert.equal(r2.status, 200);
  assert.equal(r2.data.available, false);

  const anon = await req('GET', '/api/idleyield');
  assert.equal(anon.status, 401);
});
