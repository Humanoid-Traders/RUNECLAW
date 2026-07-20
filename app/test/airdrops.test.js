'use strict';
/**
 * Airdrop & Testnet Radar (PR SS) — guided-only, never automated.
 *
 * The pins here are product-safety pins as much as correctness pins: the
 * radar must inform and checklist, and must never grow signing, wallet
 * generation, or multi-wallet mechanics (those are §4 hard lines — sybil
 * farming — and get users' activity retroactively disqualified anyway).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.AIRDROP_CATALOG_PATH;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const express = require('express');
const airdrops = require('../lib/airdrops');
const authModule = require('../auth');

// ── Catalog sanity ───────────────────────────────────────────────────────────

test('every seed campaign is complete: link, checklist, honest status', () => {
  assert.ok(airdrops.SEED_CATALOG.length >= 3);
  for (const c of airdrops.SEED_CATALOG) {
    for (const f of ['key', 'name', 'project_type', 'chains', 'status', 'costs', 'effort', 'official_url']) {
      assert.ok(c[f], `${c.key || '?'} missing ${f}`);
    }
    assert.match(c.official_url, /^https:\/\//, `${c.key} official link is https`);
    assert.ok(Array.isArray(c.steps) && c.steps.length >= 2, `${c.key} has a real checklist`);
    assert.ok(['live', 'points', 'expected'].includes(c.status), `${c.key} status is honest`);
  }
});

test('the lib contains no signing/automation primitives — guided-only is structural', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'airdrops.js'), 'utf8');
  for (const forbidden of ['signTransaction', 'sendTransaction', 'privateKey', 'mnemonic', 'createWallet', 'new Wallet(']) {
    assert.ok(!src.includes(forbidden), `lib must never contain "${forbidden}"`);
  }
});

// ── Radar assembly (pure) ────────────────────────────────────────────────────

test('anonymous radar: campaigns without hints, notes present', () => {
  const r = airdrops.buildAirdropRadar(airdrops.SEED_CATALOG, null);
  assert.equal(r.wallet_linked, false);
  assert.ok(r.campaigns.length === airdrops.SEED_CATALOG.length);
  assert.ok(r.campaigns.every(c => c.hints === null));
  assert.match(r.anti_sybil, /One human, one wallet/);
  assert.match(r.participation, /you perform and sign every step/i);
});

test('wallet hints are facts, not qualification claims', () => {
  const ctx = {
    address: '0x' + 'ab'.repeat(20),
    chains: [
      { key: 'base', readable: true, total_usd: 50 },
      { key: 'arbitrum', readable: true, total_usd: 0 },
    ],
  };
  const r = airdrops.buildAirdropRadar(airdrops.SEED_CATALOG, ctx);
  assert.equal(r.wallet_linked, true);
  const base = r.campaigns.find(c => c.key === 'base-onchain');
  assert.equal(base.hints[0].kind, 'ready');
  const arb = r.campaigns.find(c => c.key === 'arbitrum-open');
  assert.equal(arb.hints[0].kind, 'gap');
  assert.match(arb.hints[0].text, /bridge gas/);
  const testnet = r.campaigns.find(c => c.chains.includes('testnet'));
  assert.equal(testnet.hints[0].kind, 'ready');
  // No hint ever claims eligibility — that's the project's call, not ours.
  for (const c of r.campaigns) {
    for (const h of c.hints || []) {
      assert.ok(!/eligible|qualified|guaranteed/i.test(h.text), `hint overclaims: ${h.text}`);
    }
  }
});

test('operator catalog override loads; a broken file falls back to the seed', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'airdrop-cat-'));
  const good = path.join(dir, 'catalog.json');
  fs.writeFileSync(good, JSON.stringify([{
    key: 'custom', name: 'Custom Campaign', project_type: 'test', chains: ['testnet'],
    status: 'live', costs: 'free', effort: 'low', steps: ['a', 'b'],
    official_url: 'https://example.org',
  }]));
  process.env.AIRDROP_CATALOG_PATH = good;
  try {
    assert.equal(airdrops.loadCatalog()[0].key, 'custom');
    const bad = path.join(dir, 'broken.json');
    fs.writeFileSync(bad, '{not json');
    process.env.AIRDROP_CATALOG_PATH = bad;
    assert.equal(airdrops.loadCatalog(), airdrops.SEED_CATALOG);
  } finally {
    delete process.env.AIRDROP_CATALOG_PATH;
  }
});

// ── Chat intercept ───────────────────────────────────────────────────────────

test('chat: "airdrops" gets the radar WITH the anti-sybil line; small talk does not', async () => {
  const reply = await airdrops.maybeHandleAirdropChat(1, 'any good airdrops right now?');
  assert.ok(reply && reply.intent === 'airdrops');
  assert.match(reply.reply_html, /One human, one wallet/);
  assert.equal(await airdrops.maybeHandleAirdropChat(1, 'how is BTC doing?'), null);
});

test('chat: even a "farm airdrops" ask is answered with the guided-only stance', async () => {
  const reply = await airdrops.maybeHandleAirdropChat(1, 'can you farm airdrops for me?');
  assert.ok(reply, 'the farming phrasing is intercepted, not ignored');
  assert.match(reply.reply_html, /never do it|guided-only/i);
});

// ── Routes ───────────────────────────────────────────────────────────────────

let server, base;

function req(method, p, { token } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, {
      method,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/airdrops', require('../routes/airdrops'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('GET /api/airdrops is public; /me needs auth and reads only the OWN wallet', async () => {
  const pub = await req('GET', '/api/airdrops');
  assert.equal(pub.status, 200);
  assert.ok(pub.data.campaigns.length >= 3);
  assert.equal(pub.data.wallet_linked, false);

  assert.equal((await req('GET', '/api/airdrops/me')).status, 401);

  const reg = await new Promise((resolve, reject) => {
    const payload = JSON.stringify({ email: 'drop1@test.io', password: 'x'.repeat(12) });
    const r = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    r.on('error', reject); r.write(payload); r.end();
  });
  const me = await req('GET', '/api/airdrops/me', { token: reg.token });
  assert.equal(me.status, 200);
  // No wallet linked -> no hints, and the payload says so honestly.
  assert.equal(me.data.wallet_linked, false);
  assert.ok(me.data.campaigns.every(c => c.hints === null));
});
