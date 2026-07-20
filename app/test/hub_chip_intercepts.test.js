'use strict';
/**
 * Hub chip → chat intercept contract (live incident, 2026-07-20): the Hub's
 * one-tap "Meme radar" chip sent 'meme radar', which the meme intercept's
 * regex did NOT match — the ask fell through to the bot LLM, which honestly
 * told the user it has no radar access. Every radar chip's exact ask phrase
 * must be answered by its own web-side intercept, never the LLM fallback.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

test('every radar chip ask phrase is claimed by its intercept regex', async () => {
  // Chip phrases as wired in dashboard.js (pinned there by meme_panel.test.js).
  const CONTRACT = [
    ['rwa radar', require('../lib/rwa'), 'maybeHandleRwaChat', () =>
      require('../lib/rwa').setTickerFetcher(async () => ({
        ONDO: null, BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
        ONDOUSDT: { price: 1, change: 2, volume: 1e7 },
      }))],
    ['airdrop radar', require('../lib/airdrops'), 'maybeHandleAirdropChat', null],
    ['meme radar', require('../lib/meme'), 'maybeHandleMemeChat', () =>
      require('../lib/meme').setPairFetcher(async () => ([{
        chainId: 'base', dexId: 'uniswap',
        baseToken: { symbol: 'FOO', name: 'Foo', address: '0x' + '11'.repeat(20) },
        quoteToken: { symbol: 'WETH' }, priceUsd: '1', url: 'https://example.org',
        liquidity: { usd: 500000 }, volume: { h24: 100000 },
        priceChange: { h24: 2 }, pairCreatedAt: Date.now() - 30 * 86400000,
        txns: { h24: { buys: 300, sells: 280 } },
      }]))],
    ['nft radar', require('../lib/opensea'), 'maybeHandleNftChat', () =>
      require('../lib/opensea').setOpenSeaFetcher(async (p) =>
        p.startsWith('/collections?')
          ? { collections: [{ collection: 'foo', name: 'Foo' }] }
          : { total: { floor_price: 1.2, num_owners: 10 },
              intervals: [{ interval: 'seven_day', volume: 100 }] })],
  ];
  for (const [phrase, lib, fn, inject] of CONTRACT) {
    if (inject) inject();
    const reply = await lib[fn](1, phrase);
    assert.ok(reply && reply.reply_html,
      `chip ask "${phrase}" must be answered by ${fn}, not the LLM fallback`);
  }
  require('../lib/meme').setPairFetcher(null);
  require('../lib/rwa').setTickerFetcher(null);
  require('../lib/opensea').setOpenSeaFetcher(null);
});

test('the chips wired in the dashboard stay in sync with this contract', () => {
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  for (const ask of ['rwa radar', 'airdrop radar', 'meme radar', 'nft radar']) {
    assert.ok(dash.includes(`'${ask}'`), `hub chip "${ask}" exists`);
  }
});
