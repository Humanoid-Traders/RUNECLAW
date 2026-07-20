'use strict';
/**
 * NB1 — NFT collectibles in net worth: shown for context, NEVER valued
 * into the total (floors are asks, not liquidation values).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.OPENSEA_API_KEY;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const opensea = require('../lib/opensea');
const wallet = require('../lib/wallet');
const networth = require('../lib/networth');

test('collectibles listed but never summed into total_real_usd', async (t) => {
  const addr = '0x' + 'ab'.repeat(20);
  const origOf = wallet.walletAddressOf;
  const origPf = wallet.getWalletPortfolio;
  wallet.walletAddressOf = async () => addr;
  wallet.getWalletPortfolio = async () => ({
    address: addr, total_usd: 100, assets: [{}], unpriced: 0 });
  opensea.setOpenSeaFetcher(async (p) => p.includes('/account/')
    ? { nfts: [
        { name: 'Ape #1', collection: 'apes', identifier: '1' },
        { name: 'Rock #2', collection: 'rocks', identifier: '2' }] }
    : {});
  t.after(() => {
    wallet.walletAddressOf = origOf;
    wallet.getWalletPortfolio = origPf;
    opensea.setOpenSeaFetcher(null);
  });

  const n = await networth.buildNetWorth({ id: 1 }, 1);
  assert.equal(n.sections.collectibles.available, true);
  assert.equal(n.sections.collectibles.count, 2);
  assert.deepEqual(n.sections.collectibles.collections, ['apes', 'rocks']);
  assert.match(n.sections.collectibles.valuation_note, /never counted in the total/);
  assert.equal(n.total_real_usd, 100, 'total = wallet only; NFTs contribute nothing');
});

test('no key / no wallet: honest unavailable, net worth unaffected', async (t) => {
  const origOf = wallet.walletAddressOf;
  wallet.walletAddressOf = async () => null;
  t.after(() => { wallet.walletAddressOf = origOf; });
  const n = await networth.buildNetWorth({ id: 1 }, 1);
  assert.equal(n.sections.collectibles.available, false);
  assert.equal(n.sections.collectibles.reason, 'no_wallet');
});

test('structural pin: no floor value is ever added to the total', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'networth.js'), 'utf8');
  const totalBlock = src.slice(src.indexOf('// Real total'));
  assert.ok(!totalBlock.includes('collectibles'),
    'the total computation must never reference collectibles');
  assert.ok(!src.includes('floor_eth') || !totalBlock.includes('floor'),
    'no floor arithmetic near the total');
});
