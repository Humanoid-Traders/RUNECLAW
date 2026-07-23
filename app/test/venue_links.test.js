'use strict';
/**
 * Where-to-trade venue links for the Strength Map: CEX + DEX deep links for a
 * base ticker so a user can pick a platform and open the trade there.
 * Recommendations only — never auto-routing (§4).
 */
const test = require('node:test');
const assert = require('node:assert');
const { venuesFor } = require('../lib/venue_links');

test('returns both CEX and DEX venues for a normal ticker', () => {
  const v = venuesFor('SOL');
  const types = new Set(v.map((x) => x.type));
  assert.ok(types.has('CEX') && types.has('DEX'), 'both CEX and DEX offered');
  assert.ok(v.length >= 5);
  // Bitget (the map's data source) + Hyperliquid (a DEX) are present.
  assert.ok(v.some((x) => x.id === 'bitget' && x.type === 'CEX'));
  assert.ok(v.some((x) => x.id === 'hyperliquid' && x.type === 'DEX'));
});

test('deep links interpolate the base correctly and mark RUNECLAW-executable venues', () => {
  const v = venuesFor('BTC');
  const bg = v.find((x) => x.id === 'bitget');
  assert.match(bg.url, /bitget\.com\/futures\/usdt\/BTCUSDT$/);
  assert.equal(bg.runeclaw, true);
  const okx = v.find((x) => x.id === 'okx');
  assert.match(okx.url, /trade-swap\/btc-usdt-swap$/);   // OKX uses lowercase
  assert.equal(okx.runeclaw, false);
});

test('normalises a passed full symbol and lowercases/uppercases correctly', () => {
  const v = venuesFor('solusdt');   // full symbol, lowercase
  assert.ok(v.length);
  assert.ok(v.every((x) => /SOL/.test(x.url) || /sol/.test(x.url)));
});

test('rejects junk input (no venues, no thrown links)', () => {
  assert.deepEqual(venuesFor(''), []);
  assert.deepEqual(venuesFor('BTC; rm -rf'), []);   // injection attempt → empty
  assert.deepEqual(venuesFor(null), []);
});
