'use strict';
/**
 * DeFi position intelligence: Aave health factors with liquidation warnings,
 * Lido stETH, Uniswap LP counts — all from view calls against ABI-encoded
 * fake providers, per-chain fail-soft, the REST surface, the chat intercept,
 * and the read-only invariant.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB3_CHAINS;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const authModule = require('../auth');
const wallet = require('../lib/wallet');
const defi = require('../lib/defi');

const ADDR = '0x' + 'cd'.repeat(20);
const word = (v) => BigInt(v).toString(16).padStart(64, '0');
const UINT_MAX = (1n << 256n) - 1n;

// Route view calls by contract address; each protocol uses one function per
// contract here, so `to` alone disambiguates.
class FakeProvider {
  constructor(chainKey, handlers = {}, { down = false } = {}) {
    this.chainKey = chainKey; this.handlers = handlers; this.down = down;
  }
  async getBalance() { if (this.down) throw new Error('down'); return 0n; }
  async call(tx) {
    if (this.down) throw new Error('down');
    const h = this.handlers[String(tx.to).toLowerCase()];
    if (!h) return '0x' + word(0);
    return h(tx);
  }
  async getNetwork() { return { chainId: 0n }; }
  async resolveName(n) { return n; }
}

const AAVE_ETH = defi.AAVE_POOLS.ethereum.toLowerCase();
const AAVE_ARB = defi.AAVE_POOLS.arbitrum.toLowerCase();
const STETH = '0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84'.toLowerCase();
const NPM_ETH = '0xC36442b4a4522E871399CD717aBDD847Ab11FE88'.toLowerCase();

// Aave getUserAccountData: 6 uints (base currency = USD 8 decimals; HF 1e18).
const aaveData = ({ coll, debt, avail = 0, hf }) => () =>
  '0x' + word(BigInt(Math.round(coll * 1e8))) + word(BigInt(Math.round(debt * 1e8)))
       + word(BigInt(Math.round(avail * 1e8))) + word(8000n) + word(7500n)
       + word(hf === null ? UINT_MAX : BigInt(Math.round(hf * 1e18)));

const PROVIDERS = {
  // Mainnet: thin Aave position (HF 1.08 → CRITICAL), 2 stETH, 3 Uni LPs.
  ethereum: new FakeProvider('ethereum', {
    [AAVE_ETH]: aaveData({ coll: 5000, debt: 3800, avail: 120, hf: 1.08 }),
    [STETH]: () => '0x' + word(2n * 10n ** 18n),
    [NPM_ETH]: () => '0x' + word(3),
  }),
  // Arbitrum: healthy no-debt Aave position.
  arbitrum: new FakeProvider('arbitrum', {
    [AAVE_ARB]: aaveData({ coll: 1000, debt: 0, hf: null }),
  }),
  base: new FakeProvider('base', {}),
  optimism: new FakeProvider('optimism', {}, { down: true }),   // RPC dead
  polygon: new FakeProvider('polygon', {}),
};

let server, base;

test.before(async () => {
  defi.setProviderFactory((chain) => PROVIDERS[chain.key]);
  defi.setTickerFetcher(async () => ({ ETHUSDT: { price: 2500, change: 1, volume: 1e9 } }));

  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/defi', require('../routes/defi'));
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

async function newUser(tag) {
  const r = await req('POST', '/api/auth/register', {
    body: { email: `${tag}@example.com`, password: 'longenough1' },
  });
  return r.data.token;
}

test('buildDefiPositions: Aave HF + warning, no-debt chain, Lido, Uni count, dead RPC isolated', async () => {
  const d = await defi.buildDefiPositions(ADDR);
  assert.equal(d.read_only, true);

  const eth = d.aave.find(a => a.chain === 'ethereum');
  assert.equal(eth.collateral_usd, 5000);
  assert.equal(eth.debt_usd, 3800);
  assert.equal(eth.health_factor, 1.08);

  const arb = d.aave.find(a => a.chain === 'arbitrum');
  assert.equal(arb.collateral_usd, 1000);
  assert.equal(arb.health_factor, null);   // no debt → nothing to liquidate

  assert.equal(d.lido.steth_amount, 2);
  assert.equal(d.lido.usd, 5000);          // 2 × $2500, priced as ETH (stated)
  assert.match(d.lido.pricing_note, /stETH trades/);

  assert.deepEqual(d.uniswap, [{ chain: 'ethereum', label: 'Ethereum', positions: 3 }]);

  // HF 1.08 < 1.1 → CRITICAL; the healthy chain contributes no warning, and
  // optimism's dead RPC never sinks the read.
  assert.equal(d.warnings.length, 1);
  assert.match(d.warnings[0], /CRITICAL.*1\.08.*Ethereum/);
  assert.match(d.note, /never\s+repay, withdraw, or manage/);
});

test('REST: linked wallet gets positions; unlinked honest; anonymous 401', async () => {
  const token = await newUser('defiuser');
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['defiuser@example.com']);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, rows[0].id]);

  const r = await req('GET', '/api/defi', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.linked, true);
  assert.equal(r.data.aave.length, 2);
  assert.equal(r.data.warnings.length, 1);

  const token2 = await newUser('nolink');
  const r2 = await req('GET', '/api/defi', { token: token2 });
  assert.equal(r2.data.linked, false);

  assert.equal((await req('GET', '/api/defi')).status, 401);
});

test('chat: "my defi positions" reports the book with the warning; unlinked guided', async () => {
  const token = await newUser('defichat');
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['defichat@example.com']);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, rows[0].id]);

  const r = await req('POST', '/api/chat', { token, body: { text: 'what are my defi positions?' } });
  assert.equal(r.data.intent, 'defi');
  assert.match(r.data.reply_html, /Aave v3 · Ethereum/);
  assert.match(r.data.reply_html, /Health factor <b>1\.08<\/b>/);
  assert.match(r.data.reply_html, /CRITICAL/);
  assert.match(r.data.reply_html, /Lido/);
  assert.match(r.data.reply_html, /counted, not valued/);

  const token2 = await newUser('defichat2');
  const r2 = await req('POST', '/api/chat', { token: token2, body: { text: 'health factor?' } });
  assert.equal(r2.data.intent, 'defi');
  assert.match(r2.data.reply_html, /No wallet is linked/);
});

test('read-only invariant: no signing surface is exported', () => {
  for (const k of Object.keys(defi)) {
    assert.ok(!/sign|send|approve|transfer|repay|withdraw|borrow|supply/i.test(k),
      `export ${k} must not act`);
  }
});
