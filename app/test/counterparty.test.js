/**
 * Solver & Counterparty Monitor.
 *
 * Part 1 — pure concentration math (app/lib/counterparty.js): custodial vs
 * self-custody split, venue/chain HHI, largest counterparty, settlement-issuer
 * concentration, and the advisory flags. Part 2 — the /api/counterparty route
 * is JWT-gated. (The buildHoldings fan-out it reuses is covered by
 * holdings.test.js; the math is covered here.)
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'c'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const { computeCounterparty, hhi, issuerOf } = require('../lib/counterparty');

const venue = (venue, equity_usd, currency = 'USDT', ok = true) => ({ venue, ok, equity_usd, currency });
const chain = (label, total_usd) => ({ label, total_usd });

test('hhi: one bucket is 10000, two equal is 5000, empty is 0', () => {
  assert.strictEqual(hhi([100]), 10000);
  assert.strictEqual(hhi([50, 50]), 5000);
  assert.strictEqual(hhi([]), 0);
});

test('issuerOf maps settlement coins to issuers', () => {
  assert.strictEqual(issuerOf('USDT'), 'Tether');
  assert.strictEqual(issuerOf('USDC'), 'Circle');
  assert.match(issuerOf('XYZ'), /Other/);
});

test('no real balances → unrated with a no_funds flag', () => {
  const r = computeCounterparty({ venues: [venue('okx', null, 'USDT', false)], wallet: { chains: [] } });
  assert.strictEqual(r.unrated, true);
  assert.ok(r.flags.some(f => f.key === 'no_funds'));
});

test('a single dominant custodian is flagged and reads as high concentration', () => {
  const r = computeCounterparty({ venues: [venue('bybit', 1000)], wallet: { chains: [] } });
  assert.strictEqual(r.unrated, false);
  assert.strictEqual(r.custodial_pct, 100);
  assert.strictEqual(r.self_custody_pct, 0);
  assert.strictEqual(r.concentration, 'high');
  assert.strictEqual(r.hhi, 10000);
  assert.strictEqual(r.largest.label, 'bybit');
  assert.ok(r.flags.some(f => f.key === 'single_custodian'));
  assert.ok(r.flags.some(f => f.key === 'all_custodial'));
});

test('custodial vs self-custody split and largest counterparty are computed', () => {
  const r = computeCounterparty({
    venues: [venue('bybit', 300), venue('okx', 300)],
    wallet: { chains: [chain('Ethereum', 400)] },
  });
  assert.strictEqual(r.total_usd, 1000);
  assert.strictEqual(r.custodial_usd, 600);
  assert.strictEqual(r.self_custody_usd, 400);
  assert.strictEqual(r.custodial_pct, 60);
  assert.strictEqual(r.self_custody_pct, 40);
  assert.strictEqual(r.venue_count, 2);
  assert.strictEqual(r.chain_count, 1);
  assert.strictEqual(r.largest.label, 'Ethereum'); // 400 is the biggest single bucket
  assert.strictEqual(r.largest.kind, 'self_custody');
  assert.ok(!r.flags.some(f => f.key === 'all_custodial')); // healthy self-custody share
});

test('a well-spread book reads as low concentration with a diversified flag', () => {
  const r = computeCounterparty({
    venues: [venue('bybit', 250, 'USDT'), venue('hyperliquid', 250, 'USDC')],
    wallet: { chains: [chain('Ethereum', 250), chain('Base', 250)] },
  });
  assert.strictEqual(r.concentration, 'low');
  assert.ok(r.flags.some(f => f.key === 'diversified'));
});

test('single-issuer settlement is flagged; mixed issuers are split', () => {
  const allUsdt = computeCounterparty({ venues: [venue('bybit', 500), venue('okx', 500)], wallet: { chains: [] } });
  assert.strictEqual(allUsdt.issuers.length, 1);
  assert.strictEqual(allUsdt.issuers[0].issuer, 'Tether');
  assert.strictEqual(allUsdt.issuers[0].pct_of_custodial, 100);
  assert.ok(allUsdt.flags.some(f => f.key === 'issuer_concentration'));

  const mixed = computeCounterparty({ venues: [venue('bybit', 500, 'USDT'), venue('hyperliquid', 500, 'USDC')], wallet: { chains: [] } });
  const issuers = mixed.issuers.map(i => i.issuer).sort();
  assert.deepStrictEqual(issuers, ['Circle', 'Tether']);
});

test('self-custody-only book has no custodial flags and no issuers', () => {
  const r = computeCounterparty({ venues: [], wallet: { chains: [chain('Ethereum', 400), chain('Base', 600)] } });
  assert.strictEqual(r.custodial_usd, 0);
  assert.strictEqual(r.self_custody_pct, 100);
  assert.strictEqual(r.issuers.length, 0);
  assert.ok(!r.flags.some(f => f.key === 'all_custodial'));
});

test('a partial read is flagged', () => {
  const r = computeCounterparty({ venues: [venue('bybit', 500)], wallet: { chains: [chain('Ethereum', 500)] }, partial: true });
  assert.strictEqual(r.partial, true);
  assert.ok(r.flags.some(f => f.key === 'partial'));
});

// ── Part 2: the route is JWT-gated ───────────────────────────────────────

test('GET /api/counterparty requires a JWT', async () => {
  const express = require('express');
  const app = express();
  app.use('/api/counterparty', require('../routes/counterparty'));
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const status = await new Promise((resolve, reject) => {
    http.get(`${base}/api/counterparty`, (res) => { res.resume(); resolve(res.statusCode); }).on('error', reject);
  });
  server.close();
  assert.strictEqual(status, 401);
});
