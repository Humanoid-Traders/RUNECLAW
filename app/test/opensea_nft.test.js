'use strict';
/**
 * OpenSea read-only NFT surface (#343). Contract: honest not_configured
 * without a key; radar ranked by real volume with injected fetcher; wallet
 * mirror validates addresses; and — the hard line — NO marketplace
 * machinery anywhere (source grep).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.OPENSEA_API_KEY;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const opensea = require('../lib/opensea');

function fakeFetcher(p) {
  if (p.startsWith('/collections?')) {
    return Promise.resolve({ collections: [
      { collection: 'apes', name: 'Apes' }, { collection: 'rocks', name: 'Rocks' }] });
  }
  if (p.includes('/stats')) {
    return Promise.resolve({ total: { floor_price: 2.5, num_owners: 500 },
      intervals: [{ interval: 'seven_day', volume: 321 }] });
  }
  if (p.includes('/account/')) {
    return Promise.resolve({ nfts: [
      { name: 'Ape #1', collection: 'apes', identifier: '1', image_url: 'https://x/1.png' }] });
  }
  return Promise.resolve({});
}

test('without a key: honest not_configured, never a fabricated radar', async () => {
  opensea.setOpenSeaFetcher(null);
  const r = await opensea.getNftRadar();
  assert.equal(r.available, false);
  assert.equal(r.reason, 'not_configured');
  const w = await opensea.getWalletNfts('0x' + 'ab'.repeat(20));
  assert.equal(w.available, false);
});

test('radar: ranked by real 7-day volume with floor/owners, disclaimer attached', async () => {
  opensea.setOpenSeaFetcher(fakeFetcher);
  const r = await opensea.getNftRadar();
  assert.equal(r.available, true);
  assert.match(r.ranked_by, /seven_day_volume/);
  assert.equal(r.entries[0].slug, 'apes');
  assert.equal(r.entries[0].floor_eth, 2.5);
  assert.equal(r.entries[0].seven_day_volume, 321);
  assert.match(r.disclaimer, /never lists, bids, mints or trades/);
  opensea.setOpenSeaFetcher(null);
});

test('wallet mirror: address validated, items mapped, read-only note', async () => {
  opensea.setOpenSeaFetcher(fakeFetcher);
  const bad = await opensea.getWalletNfts('vitalik.eth');
  assert.equal(bad.reason, 'bad_address');
  const w = await opensea.getWalletNfts('0x' + 'AB'.repeat(20));
  assert.equal(w.available, true);
  assert.equal(w.count, 1);
  assert.equal(w.items[0].name, 'Ape #1');
  assert.match(w.note, /never moves NFTs/);
  opensea.setOpenSeaFetcher(null);
});

test('chat intercept answers "nft radar" and stays quiet otherwise', async () => {
  opensea.setOpenSeaFetcher(fakeFetcher);
  const reply = await opensea.maybeHandleNftChat(1, 'nft radar');
  assert.ok(reply && reply.reply_html.includes('NFT radar'));
  assert.equal(await opensea.maybeHandleNftChat(1, 'hello there'), null);
  opensea.setOpenSeaFetcher(null);
});

test('HARD LINE: no marketplace machinery in the NFT surface', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'opensea.js'), 'utf8')
    + fs.readFileSync(path.join(__dirname, '..', 'routes', 'nft.js'), 'utf8');
  for (const forbidden of ['createListing', 'fulfillOrder', 'seaport', 'Seaport',
    'placeBid', 'createOffer', 'privateKey', 'PRIVATE_KEY', 'signTransaction',
    'sendTransaction', '/listings', '/offers']) {
    assert.ok(!src.includes(forbidden), `NFT surface must never contain ${forbidden}`);
  }
});
