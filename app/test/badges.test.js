/**
 * Wallet-native on-chain badges.
 *
 * Part 1 — pure badge derivation (app/lib/badges.js): each badge is earned only
 * from a real, checkable holding; thresholds; empty context; determinism.
 * Part 2 — GET /api/web3/profile is JWT-gated.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'b'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const { computeBadges } = require('../lib/badges');

function byKey(res, key) { return res.badges.find(b => b.key === key); }

test('an empty wallet earns nothing but still lists every badge as locked', () => {
  const r = computeBadges({});
  assert.strictEqual(r.earned, 0);
  assert.strictEqual(r.total, r.badges.length);
  assert.ok(r.badges.every(b => b.earned === false));
});

test('ENS, land, collector, multichain and DeFi badges each require their fact', () => {
  const r = computeBadges({
    ens: 'trader.eth',
    landKinds: ['land', 'wearable'],
    nftCount: 8,
    chainsWithBalance: 3,
    assetSymbols: ['USDC', 'AAVE'],
  });
  assert.strictEqual(byKey(r, 'ens').earned, true);
  assert.match(byKey(r, 'ens').detail, /trader\.eth/);
  assert.strictEqual(byKey(r, 'landholder').earned, true);
  assert.strictEqual(byKey(r, 'collector').earned, true);
  assert.strictEqual(byKey(r, 'multichain').earned, true);
  assert.strictEqual(byKey(r, 'defi_native').earned, true);
  assert.strictEqual(r.earned, 5);
});

test('thresholds gate collector (>=5 NFTs) and multichain (>=2 chains)', () => {
  const few = computeBadges({ nftCount: 4, chainsWithBalance: 1 });
  assert.strictEqual(byKey(few, 'collector').earned, false);
  assert.strictEqual(byKey(few, 'multichain').earned, false);
  const enough = computeBadges({ nftCount: 5, chainsWithBalance: 2 });
  assert.strictEqual(byKey(enough, 'collector').earned, true);
  assert.strictEqual(byKey(enough, 'multichain').earned, true);
});

test('DeFi badge matches symbols case-insensitively and ignores plain stables', () => {
  assert.strictEqual(byKey(computeBadges({ assetSymbols: ['pendle'] }), 'defi_native').earned, true);
  assert.strictEqual(byKey(computeBadges({ assetSymbols: ['USDT', 'USDC'] }), 'defi_native').earned, false);
});

test('wearable-only holdings do not earn the Landholder badge', () => {
  const r = computeBadges({ landKinds: ['wearable', 'name'] });
  assert.strictEqual(byKey(r, 'landholder').earned, false);
});

test('badge derivation is deterministic', () => {
  const ctx = { ens: 'a.eth', nftCount: 6, chainsWithBalance: 2, assetSymbols: ['ARB'], landKinds: ['land'] };
  assert.deepStrictEqual(computeBadges(ctx), computeBadges(ctx));
});

// ── Part 2: route gating ─────────────────────────────────────────────────

test('GET /api/web3/profile requires a JWT', async () => {
  const express = require('express');
  const app = express();
  app.use('/api/web3', require('../routes/web3'));
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const status = await new Promise((resolve, reject) => http.get(`${base}/api/web3/profile`, (res) => { res.resume(); resolve(res.statusCode); }).on('error', reject));
  server.close();
  assert.strictEqual(status, 401);
});
