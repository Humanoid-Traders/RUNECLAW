/**
 * Web3 identity & metaverse worlds.
 *
 * Part 1 — pure worlds classifier (app/lib/worlds.js): recognises metaverse
 * LAND / names / wearables by OpenSea collection slug, links to the world, and
 * leaves unknown collectibles as "other" (never mislabelled as land).
 * Part 2 — ENS resolver (app/lib/ens.js) with an injected fake provider:
 * reverse-name + avatar, honest address fallback, no network.
 * Part 3 — /api/web3 routes are JWT-gated.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'w'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const { classifyWorlds, worldFor } = require('../lib/worlds');
const ens = require('../lib/ens');

// ── Part 1: worlds classifier ────────────────────────────────────────────

test('worldFor recognises known metaverse slugs, case-insensitively', () => {
  assert.strictEqual(worldFor('sandbox').world, 'The Sandbox');
  assert.strictEqual(worldFor('DECENTRALAND').world, 'Decentraland');
  assert.strictEqual(worldFor('otherdeed-for-otherside').world, 'Otherside');
  assert.strictEqual(worldFor('some-random-pfp'), null);
});

test('classifyWorlds splits worlds from other collectibles and links out', () => {
  const items = [
    { name: 'LAND (-12, 44)', collection: 'sandbox', token_id: '1', image_url: 'x' },
    { name: 'Parcel', collection: 'decentraland', token_id: '2', image_url: 'y' },
    { name: 'Cool Cat #7', collection: 'cool-cats-nft', token_id: '7', image_url: 'z' },
  ];
  const r = classifyWorlds(items);
  assert.strictEqual(r.worlds.length, 2);
  assert.strictEqual(r.other.length, 1);
  assert.strictEqual(r.other[0].collection, 'cool-cats-nft');
  assert.strictEqual(r.world_count, 2);
  const sandbox = r.worlds.find(w => w.world === 'The Sandbox');
  assert.strictEqual(sandbox.kind, 'land');
  assert.match(sandbox.url, /sandbox\.game/);
});

test('classifyWorlds summary rolls up per-world counts', () => {
  const items = [
    { collection: 'sandbox', token_id: '1' },
    { collection: 'sandbox', token_id: '2' },
    { collection: 'decentraland-wearables', token_id: '3' },
  ];
  const r = classifyWorlds(items);
  const sb = r.summary.find(s => s.world === 'The Sandbox');
  assert.strictEqual(sb.count, 2);
  const dcl = r.summary.find(s => s.world === 'Decentraland');
  assert.deepStrictEqual(dcl.kinds, ['wearable']);
});

test('classifyWorlds handles empty / non-array input', () => {
  assert.deepStrictEqual(classifyWorlds(null).worlds, []);
  assert.strictEqual(classifyWorlds(undefined).world_count, 0);
});

// ── Part 2: ENS resolver with a fake provider ────────────────────────────

const ADDR = '0x' + '12'.repeat(20);

test('resolveIdentity returns ENS name + avatar when set', async () => {
  ens.setEnsProviderFactory(() => ({
    async lookupAddress() { return 'vitalik.eth'; },
    async getAvatar() { return 'https://img/avatar.png'; },
  }));
  const id = await ens.resolveIdentity(ADDR);
  assert.strictEqual(id.ens, 'vitalik.eth');
  assert.strictEqual(id.avatar, 'https://img/avatar.png');
  assert.strictEqual(id.resolved, true);
  assert.match(id.short, /^0x12.*12$/);
});

test('resolveIdentity falls back to the short address when no ENS set', async () => {
  ens.setEnsProviderFactory(() => ({
    async lookupAddress() { return null; },
    async getAvatar() { return null; },
  }));
  const id = await ens.resolveIdentity(ADDR);
  assert.strictEqual(id.ens, null);
  assert.strictEqual(id.resolved, true); // the lookup ran; it just has no name
  assert.ok(id.short.includes('…'));
});

test('resolveIdentity is safe on a bad address', async () => {
  const id = await ens.resolveIdentity('not-an-address');
  assert.strictEqual(id.address, null);
  assert.strictEqual(id.resolved, false);
});

test('resolveIdentity survives an RPC error (address-only, resolved=false)', async () => {
  ens.setEnsProviderFactory(() => ({ async lookupAddress() { throw new Error('rpc down'); } }));
  const id = await ens.resolveIdentity('0x' + 'ab'.repeat(20));
  assert.strictEqual(id.ens, null);
  assert.strictEqual(id.resolved, false);
  assert.ok(id.short);
});

// ── Part 3: routes are JWT-gated ─────────────────────────────────────────

test('GET /api/web3/identity and /collectibles require a JWT', async () => {
  const express = require('express');
  const app = express();
  app.use('/api/web3', require('../routes/web3'));
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const get = (p) => new Promise((resolve, reject) => http.get(`${base}${p}`, (res) => { res.resume(); resolve(res.statusCode); }).on('error', reject));
  const a = await get('/api/web3/identity');
  const b = await get('/api/web3/collectibles');
  server.close();
  assert.strictEqual(a, 401);
  assert.strictEqual(b, 401);
});
