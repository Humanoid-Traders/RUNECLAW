'use strict';
/**
 * PR W — DEX-first UX + read-only wallet portfolio.
 *
 * Pins: wallet reads are strictly read-only mirrors (fake provider — the
 * module only ever calls getBalance/balanceOf), pricing via venue tickers
 * with stables pinned, no-wallet and unpriced honesty, the DEX↔CEX basis
 * math, and both chat intercepts.
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
const dex = require('../lib/dex');

let server, base;

const TICKERS = {
  BTCUSDT: { price: 100_000, change: 1.0, volume: 1e9 },
  ETHUSDT: { price: 2_500, change: 2.0, volume: 1e9 },
  ONDOUSDT: { price: 1.0, change: 5.0, volume: 1e7 },
  SOLUSDT: { price: 150, change: 0.5, volume: 1e8 },
};

// Fake ethers provider: 1 ETH native; balances by contract address.
const ONDO_ADDR = wallet.TOKENS.find(t => t.symbol === 'ONDO').address;
const USDC_ADDR = wallet.TOKENS.find(t => t.symbol === 'USDC').address;
class FakeProvider {
  async getBalance() { return 10n ** 18n; }                    // 1 ETH
  // ethers.Contract calls provider.call({to, data}) under the hood.
  async call(tx) {
    const to = String(tx.to).toLowerCase();
    let raw = 0n;
    if (to === ONDO_ADDR.toLowerCase()) raw = 250n * 10n ** 18n;   // 250 ONDO
    if (to === USDC_ADDR.toLowerCase()) raw = 1_000n * 10n ** 6n;  // 1000 USDC
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  // ethers v6 provider plumbing used by Contract:
  async getNetwork() { return { chainId: 1n }; }
  async resolveName(n) { return n; }
}

test.before(async () => {
  wallet.setProviderFactory(() => new FakeProvider());
  wallet.setTickerFetcher(async () => TICKERS);
  dex.setTickerFetcher(async () => TICKERS);
  dex.setMidsFetcher(async () => ({ BTC: '100050', ETH: '2499', SOL: '150.15', HYPE: '30' }));

  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/wallet', require('../routes/wallet'));
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

let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `w${seq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return { token: r.data.token, id: r.data.user?.id ?? r.data.user_id };
}

const ADDR = '0x' + 'ab'.repeat(20);

async function linkWallet(token) {
  // Resolve the caller's id from /me, then seed the SIWE link the way the
  // real flow stores it.
  const me = await req('GET', '/api/auth/me', { token });
  const uid = me.data.id ?? me.data.user_id;
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, uid]);
  return uid;
}

// ── Wallet portfolio ─────────────────────────────────────────────────────────

test('wallet: read-only mirror prices native + ERC-20s off venue tickers', async () => {
  const p = await wallet.getWalletPortfolio(ADDR);
  assert.equal(p.read_only, true);
  const by = Object.fromEntries(p.assets.map(a => [a.symbol, a]));
  assert.equal(by.ETH.amount, 1);
  assert.equal(by.ETH.usd, 2500);
  assert.equal(by.ONDO.amount, 250);
  assert.equal(by.ONDO.usd, 250);          // 250 × $1.00
  assert.equal(by.USDC.amount, 1000);
  assert.equal(by.USDC.usd, 1000);         // stable pinned at $1
  assert.equal(p.total_usd, 3750);
  assert.equal(p.unpriced, 0);
  // Zero-balance tokens are omitted entirely.
  assert.ok(!by.WBTC && !by.PENDLE);
});

test('wallet: invalid address → null; endpoint requires auth + linked wallet', async () => {
  assert.equal(await wallet.getWalletPortfolio('not-an-address'), null);

  const anon = await req('GET', '/api/wallet/portfolio');
  assert.equal(anon.status, 401);

  const { token } = await newUser();
  const unlinked = await req('GET', '/api/wallet/portfolio', { token });
  assert.equal(unlinked.status, 200);
  assert.equal(unlinked.data.linked, false);
  assert.equal(unlinked.data.address, null);

  await linkWallet(token);
  const linked = await req('GET', '/api/wallet/portfolio', { token });
  assert.equal(linked.status, 200);
  assert.equal(linked.data.linked, true);
  assert.equal(linked.data.total_usd, 3750);
  assert.equal(linked.data.read_only, true);
});

// ── DEX ↔ CEX comparison ─────────────────────────────────────────────────────

test('dex: basis math in bps; DEX-only coins keep a row, unlisted omitted', () => {
  const cmp = dex.buildCompare(
    { BTC: '100050', ETH: '2499', HYPE: '30' }, TICKERS);
  const by = Object.fromEntries(cmp.rows.map(r => [r.base, r]));
  // BTC: (100050-100000)/100000 × 10000 = +5 bps
  assert.equal(by.BTC.delta_bps, 5);
  // ETH: (2499-2500)/2500 × 10000 = -4 bps
  assert.equal(by.ETH.delta_bps, -4);
  // HYPE has no CEX ticker → row kept with delta null (DEX-native listing).
  assert.equal(by.HYPE.cex_price, null);
  assert.equal(by.HYPE.delta_bps, null);
  // SOL absent from mids → omitted.
  assert.ok(!by.SOL);
  assert.equal(cmp.avg_abs_delta_bps, 4.5);
  assert.equal(cmp.read_only, true);
  assert.match(cmp.execution_note, /design-only/);
});

test('GET /api/market/dex is public and carries the comparison', async () => {
  const r = await req('GET', '/api/market/dex');
  assert.equal(r.status, 200);
  assert.equal(r.data.read_only, true);
  assert.ok(r.data.rows.length >= 3);
});

// ── Chat intercepts ──────────────────────────────────────────────────────────

test('chat: "my wallet" — nudge when unlinked, mirror when linked', async () => {
  const { token } = await newUser();
  const nudge = await req('POST', '/api/chat', { token, body: { text: 'show my wallet balance' } });
  assert.equal(nudge.data.intent, 'wallet');
  assert.match(nudge.data.reply_html, /Sign-In with Ethereum/);

  await linkWallet(token);
  const r = await req('POST', '/api/chat', { token, body: { text: 'my wallet' } });
  assert.equal(r.data.intent, 'wallet');
  assert.match(r.data.reply_html, /0xabab…abab/);
  assert.match(r.data.reply_html, /\$3,750/);
  assert.match(r.data.reply_html, /can never move them/);

  const other = await req('POST', '/api/chat', { token, body: { text: 'good morning' } });
  assert.equal(other.status, 503);   // unconfigured bot proxy
});
