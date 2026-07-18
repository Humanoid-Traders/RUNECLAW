'use strict';
/**
 * Multi-chain wallet mirror: per-chain reads with independent fail-soft,
 * combined totals, the flattened compatibility shape, chain filtering in
 * chat, and the WEB3_CHAINS trim. Strictly read-only throughout.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB3_CHAINS;   // all chains active in this file

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const wallet = require('../lib/wallet');

const ADDR = '0x' + 'ab'.repeat(20);

// Per-chain fake providers: ethereum holds ETH + USDC, base holds USDC,
// arbitrum's RPC is DOWN, optimism/polygon are empty.
class FakeProvider {
  constructor(chainKey, { down = false, native = 0n, balances = {} } = {}) {
    this.chainKey = chainKey; this.down = down;
    this.native = native; this.balances = balances; // addressLower -> bigint
  }
  async getBalance() {
    if (this.down) throw new Error('rpc down');
    return this.native;
  }
  async call(tx) {
    if (this.down) throw new Error('rpc down');
    const raw = this.balances[String(tx.to).toLowerCase()] ?? 0n;
    return '0x' + raw.toString(16).padStart(64, '0');
  }
  async getNetwork() { return { chainId: 0n }; }
  async resolveName(n) { return n; }
}

const USDC_ETH = '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48'.toLowerCase();
const USDC_BASE = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'.toLowerCase();

const PROVIDERS = {
  ethereum: new FakeProvider('ethereum', {
    native: 2n * 10n ** 18n,                       // 2 ETH
    balances: { [USDC_ETH]: 500n * 10n ** 6n },    // 500 USDC
  }),
  base: new FakeProvider('base', {
    balances: { [USDC_BASE]: 250n * 10n ** 6n },   // 250 USDC
  }),
  arbitrum: new FakeProvider('arbitrum', { down: true }),
  optimism: new FakeProvider('optimism', {}),
  polygon: new FakeProvider('polygon', {}),
};

test.before(async () => {
  wallet.setProviderFactory((chain) => PROVIDERS[chain.key]);
  wallet.setTickerFetcher(async () => ({
    ETHUSDT: { price: 2500, change: 1, volume: 1e9 },
    BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
  }));
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['mc@example.com', 'x'.repeat(60)]);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['mc@example.com']);
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?', [ADDR, rows[0].id]);
  this.userId = rows[0].id;
});

test('portfolio: per-chain sections, combined total, down chain isolated', async () => {
  const p = await wallet.getWalletPortfolio(ADDR);
  assert.ok(p && p.read_only === true);
  assert.equal(p.chain, 'multi');
  assert.equal(p.chains.length, 5);

  const eth = p.chains.find(c => c.chain === 'ethereum');
  // 2 ETH * 2500 + 500 USDC = 5500.
  assert.equal(eth.total_usd, 5500);
  assert.deepEqual(eth.assets.map(a => a.symbol).sort(), ['ETH', 'USDC']);

  const base = p.chains.find(c => c.chain === 'base');
  assert.equal(base.total_usd, 250);

  const arb = p.chains.find(c => c.chain === 'arbitrum');
  assert.equal(arb.assets.length, 0);
  assert.equal(arb.error, 'rpc unreadable');    // down RPC is flagged, not fatal

  // Combined + flattened compatibility shape (assets carry their chain).
  assert.equal(p.total_usd, 5750);
  assert.equal(p.assets.length, 3);
  assert.ok(p.assets.every(a => a.chain));
  assert.equal(p.assets[0].symbol, 'ETH');      // sorted by USD desc
});

test('chat: multi-chain breakdown with the down chain named', async (t) => {
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['mc@example.com']);
  const r = await wallet.maybeHandleWalletChat(rows[0].id, 'show my wallet please');
  assert.equal(r.intent, 'wallet');
  assert.match(r.reply_html, /Ethereum/);
  assert.match(r.reply_html, /Base/);
  assert.match(r.reply_html, /\$5,750/);
  assert.match(r.reply_html, /Arbitrum.*unreadable/);
  assert.match(r.reply_html, /never move them/);
});

test('chat: "my wallet on base" filters to that chain; unknown chain honest', async () => {
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['mc@example.com']);
  const r = await wallet.maybeHandleWalletChat(rows[0].id, 'my wallet on base');
  assert.match(r.reply_html, /Base/);
  assert.ok(!/Ethereum/.test(r.reply_html), 'other chains filtered out');
  assert.match(r.reply_html, /\$250/);

  const un = await wallet.maybeHandleWalletChat(rows[0].id, 'my wallet on solana');
  assert.match(un.reply_html, /don't mirror/);
  assert.match(un.reply_html, /Ethereum, Base, Arbitrum, Optimism, Polygon/);
});

test('WEB3_CHAINS trims the sweep (and invalid values fall back to all)', () => {
  process.env.WEB3_CHAINS = 'ethereum,base';
  assert.deepEqual(wallet.activeChains().map(c => c.key), ['ethereum', 'base']);
  process.env.WEB3_CHAINS = 'nonsense';
  assert.equal(wallet.activeChains().length, 5);
  delete process.env.WEB3_CHAINS;
});

test('read-only invariant: no signing surface is exported', () => {
  for (const k of Object.keys(wallet)) {
    assert.ok(!/sign|send|approve|transfer/i.test(k), `export ${k} must not act`);
  }
});
