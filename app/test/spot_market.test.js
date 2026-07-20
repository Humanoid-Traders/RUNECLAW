'use strict';
/** SPOT-1 — read-only spot market center. No order machinery (source grep). */
process.env.JWT_SECRET = 'j'.repeat(64);
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const spot = require('../lib/spot');
const tickers = require('../lib/tickers');

const RAW = { data: [
  { symbol: 'BTCUSDT', lastPr: '100000', change24h: '0.012', usdtVolume: '2000000000', high24h: '101000', low24h: '98000' },
  { symbol: 'ETHUSDT', lastPr: '4000', change24h: '-0.02', usdtVolume: '900000000' },
  { symbol: 'BTCEUR', lastPr: '90000', change24h: '0', usdtVolume: '1' },
  { symbol: 'JUNKUSDT', lastPr: '0', change24h: '0', usdtVolume: '5' },
] };

test('spot market: USDT pairs only, volume-ranked, junk dropped', async () => {
  spot.setSpotFetcher(async () => RAW);
  const m = await spot.getSpotMarket();
  assert.equal(m.available, true);
  assert.deepEqual(m.pairs.map(p => p.symbol), ['BTCUSDT', 'ETHUSDT']);
  assert.equal(m.pairs[0].change_pct, 1.2);
  assert.equal(m.pairs[0].base, 'BTC');
  assert.match(m.note, /places no spot orders/);
  spot.setSpotFetcher(null);
});

test('spot-perp basis joins both books in bps', async () => {
  spot.setSpotFetcher(async () => RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1 } }));
  const b = await spot.getSpotPerpBasis();
  assert.equal(b.available, true);
  assert.equal(b.rows.length, 1);
  assert.ok(Math.abs(b.rows[0].basis_bps - 10.0) < 0.2, String(b.rows[0].basis_bps));
  spot.setSpotFetcher(null); tickers.setTickerFetcher(null);
});

test('unreachable venue reads honestly unavailable', async () => {
  spot.setSpotFetcher(async () => { throw new Error('down'); });
  const m = await spot.getSpotMarket();
  assert.equal(m.available, false);
  assert.equal(m.reason, 'unreachable');
  spot.setSpotFetcher(null);
});

test('chat intercept answers its chip phrase with market + basis', async () => {
  spot.setSpotFetcher(async () => RAW);
  tickers.setTickerFetcher(async () => ({ BTCUSDT: { price: 99900, change: 1, volume: 1 } }));
  const r = await spot.maybeHandleSpotChat(1, 'spot market');
  assert.ok(r && r.reply_html.includes('Spot market'));
  assert.ok(r.reply_html.includes('basis'));
  assert.equal(await spot.maybeHandleSpotChat(1, 'hello'), null);
  spot.setSpotFetcher(null); tickers.setTickerFetcher(null);
});

test('HARD LINE: no order machinery in the spot surface', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'spot.js'), 'utf8')
    + fs.readFileSync(path.join(__dirname, '..', 'routes', 'spot.js'), 'utf8');
  for (const forbidden of ['placeOrder', 'createOrder', 'submitOrder', '/api/trade',
    'privateKey', 'apiKey', 'API_SECRET', 'signTransaction']) {
    assert.ok(!src.includes(forbidden), `spot surface must never contain ${forbidden}`);
  }
});
