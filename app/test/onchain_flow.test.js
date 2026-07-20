'use strict';
/**
 * On-chain flow radar (PR JJ) — keyless DEX taker-flow reads for the majors.
 * Pins: honest aggregation (junk pools excluded, thin samples damped and
 * labeled), the NOT-netflow honesty note on the wire, the public endpoint,
 * and the bot-secret sync read the engine's gated voter consumes.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const flow = require('../lib/onchain_flow');

function pair(over = {}) {
  return Object.assign({
    baseToken: { symbol: 'WBTC' },
    liquidity: { usd: 5_000_000 },
    volume: { h24: 2_000_000 },
    txns: { h24: { buys: 600, sells: 400 } },
  }, over);
}

test('flowRow aggregates deep pools; junk pools and squatters are excluded', () => {
  const rows = [
    pair(),
    pair({ txns: { h24: { buys: 100, sells: 300 } } }),
    pair({ liquidity: { usd: 5_000 } }),                    // junk: below floor
    pair({ baseToken: { symbol: 'WBTCX' } }),               // squatter symbol
  ];
  const r = flow.flowRow({ base: 'BTC', dex_symbol: 'WBTC' }, rows);
  assert.equal(r.pairs, 2);
  assert.equal(r.buys_24h, 700);
  assert.equal(r.sells_24h, 700);
  assert.equal(r.flow_bias, 0);                             // perfectly balanced
  assert.equal(r.sample, 'ok');
});

test('thin samples are damped toward zero and labeled, never dressed up', () => {
  const r = flow.flowRow({ base: 'SOL', dex_symbol: 'SOL' }, [
    pair({ baseToken: { symbol: 'SOL' }, txns: { h24: { buys: 40, sells: 10 } } }),
  ]);
  assert.equal(r.sample, 'thin');
  // Raw bias would be +0.6; 50/200 txns damps it to a quarter strength.
  assert.equal(r.flow_bias, 0.15);
});

test('buildFlowRadar: covered bases get rows, the rest are listed unavailable', () => {
  const radar = flow.buildFlowRadar({
    BTC: [pair()],
    ETH: [pair({ baseToken: { symbol: 'WETH' }, txns: { h24: { buys: 900, sells: 300 } } })],
  });
  assert.equal(radar.bases.length, 2);
  assert.ok(radar.unavailable.includes('SOL'));
  assert.match(radar.note, /NOT exchange netflow/);
  assert.equal(radar.read_only, true);
  const eth = radar.bases.find(b => b.base === 'ETH');
  assert.equal(eth.flow_bias, 0.5);                          // (2*0.75-1), full sample
});

let server, base;

test.before(async () => {
  flow.setPairSearcher(async (sym) => (sym === 'WBTC' ? [pair()] : null));
  const app = express();
  app.use(express.json());
  app.use('/api/market', require('../routes/market'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); flow.setPairSearcher(null); });

function get(p, headers) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { headers: headers || {} }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

test('public endpoint serves the radar; sync read requires the bot secret', async () => {
  const pub = await get('/api/market/onchain-flow');
  assert.equal(pub.status, 200);
  assert.equal(pub.data.bases.length, 1);
  assert.equal(pub.data.bases[0].base, 'BTC');

  const noAuth = await get('/api/bot/sync/onchain-flow');
  assert.equal(noAuth.status, 403);
  const authed = await get('/api/bot/sync/onchain-flow',
    { 'x-bot-secret': process.env.BOT_SYNC_SECRET });
  assert.equal(authed.status, 200);
  assert.deepEqual(authed.data.bases[0].base, 'BTC');
});
