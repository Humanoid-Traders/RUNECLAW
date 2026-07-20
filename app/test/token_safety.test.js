'use strict';
/**
 * Token safety scanner (PR KK) — deterministic heuristic flags, never a
 * verdict. Pinned here: the flag thresholds, the honest "no flags ≠ safe"
 * framing, fail-soft on-chain degradation, and the fold into the research
 * dossier's sections.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const safety = require('../lib/token_safety');
const research = require('../lib/research');
const rwa = require('../lib/rwa');
const dexLib = require('../lib/dex');

function pairFixture(over = {}) {
  return Object.assign({
    chainId: 'base', dexId: 'uniswap',
    baseToken: { symbol: 'FOO', name: 'Foo', address: '0x' + '11'.repeat(20) },
    quoteToken: { symbol: 'WETH' },
    priceUsd: '1.00',
    liquidity: { usd: 500000 },
    volume: { h24: 100000 },
    priceChange: { h24: 2 },
    pairCreatedAt: Date.now() - 90 * 24 * 3600 * 1000,   // 90 days old
    txns: { h24: { buys: 300, sells: 280 } },
  }, over);
}

// ── Pure heuristics ──────────────────────────────────────────────────────────

test('clean large-cap: standard tier, zero flags, honest disclaimer', () => {
  const r = safety.buildSafetyRead({
    base: 'btc',
    ticker: { price: 100000, change: 1.2, volume: 5e9 },
    pair: pairFixture({ baseToken: { symbol: 'BTC' }, priceUsd: '100100' }),
  });
  assert.equal(r.base, 'BTC');
  assert.equal(r.tier, 'standard');
  assert.equal(r.flags.length, 0);
  assert.match(r.disclaimer, /never a verdict/i);
  assert.match(r.disclaimer, /not that the token is safe/i);
});

test('thin venue volume and extreme moves flag with plain-language reasons', () => {
  const r = safety.buildSafetyRead({
    base: 'XYZ', ticker: { price: 0.5, change: 31, volume: 800000 }, pair: null,
  });
  const keys = r.flags.map(f => f.key);
  assert.ok(keys.includes('thin-cex-volume'));
  assert.ok(keys.includes('extreme-24h-move'));
  assert.equal(r.tier, 'high');
  // No pair -> the payload SAYS on-chain checks did not run.
  assert.ok(r.notes.some(n => /on-chain checks did not run/.test(n)));
  assert.equal(r.checks_run.onchain, false);
});

test('honeypot pattern (no sells) and fresh pair escalate to extreme', () => {
  const r = safety.buildSafetyRead({
    base: 'RUG',
    ticker: { price: 1, change: 5, volume: 5e7 },
    pair: pairFixture({
      baseToken: { symbol: 'RUG' },
      liquidity: { usd: 8000 },
      pairCreatedAt: Date.now() - 3600 * 1000,           // 1h old
      txns: { h24: { buys: 40, sells: 0 } },
    }),
  });
  const keys = r.flags.map(f => f.key);
  assert.ok(keys.includes('very-low-liquidity'));
  assert.ok(keys.includes('under-24h-old'));
  assert.ok(keys.includes('no-sells-yet'));
  assert.equal(r.tier, 'extreme');
  assert.match(r.flags.find(f => f.key === 'no-sells-yet').text, /honeypot/);
});

test('parabolic move alone is extreme; wide CEX-DEX gap flags', () => {
  const r = safety.buildSafetyRead({
    base: 'PMP',
    ticker: { price: 100, change: 72, volume: 3e7 },
    pair: pairFixture({ baseToken: { symbol: 'PMP' }, priceUsd: '82' }),   // 18% gap
  });
  const keys = r.flags.map(f => f.key);
  assert.ok(keys.includes('parabolic-24h-move'));
  assert.ok(keys.includes('wide-cex-dex-gap'));
  assert.equal(r.tier, 'extreme');
});

test('bestPairFor: exact symbol only, deepest liquidity wins', () => {
  const shallow = pairFixture({ liquidity: { usd: 1000 } });
  const deep = pairFixture({ liquidity: { usd: 900000 } });
  const squatter = pairFixture({ baseToken: { symbol: 'FOOX' }, liquidity: { usd: 9e9 } });
  assert.equal(safety.bestPairFor('FOO', [shallow, squatter, deep]), deep);
  assert.equal(safety.bestPairFor('FOO', [squatter]), null);
  assert.equal(safety.bestPairFor('FOO', null), null);
});

test('scanToken degrades to CEX-only when the pair search throws', async () => {
  safety.setPairSearcher(async () => { throw new Error('network down'); });
  try {
    const r = await safety.scanToken('BTC', { ticker: { price: 100000, change: 1, volume: 5e9 } });
    assert.equal(r.tier, 'standard');
    assert.equal(r.checks_run.onchain, false);
  } finally {
    safety.setPairSearcher(null);
  }
});

// ── Fold into the research dossier ───────────────────────────────────────────

test('dossier carries the Safety read section and the structured safety payload', async () => {
  const tickers = { BTCUSDT: { price: 100000, change: 1, volume: 5e9 } };
  research.setTickerFetcher(async () => tickers);
  rwa.setTickerFetcher(async () => tickers);
  dexLib.setTickerFetcher(async () => tickers);
  dexLib.setMidsFetcher(async () => ({}));
  safety.setPairSearcher(async () => [pairFixture({ baseToken: { symbol: 'BTC' }, priceUsd: '100100' })]);
  try {
    const d = await research.buildDossier('BTC');
    assert.ok(d, 'dossier builds');
    const sec = d.sections.find(s => s.title === 'Safety read');
    assert.ok(sec, 'safety section present');
    assert.match(sec.html, /no heuristic flags/);
    assert.match(sec.html, /not a safety guarantee/);
    assert.equal(d.safety.tier, 'standard');
    assert.ok(d.sources.some(s => /token safety heuristics/.test(s)));
  } finally {
    research.setTickerFetcher(null);
    rwa.setTickerFetcher(null);
    dexLib.setTickerFetcher(null);
    dexLib.setMidsFetcher(null);
    safety.setPairSearcher(null);
  }
});

test('an unreachable safety scan never blocks the dossier', async () => {
  const tickers = { ETHUSDT: { price: 4000, change: 2, volume: 3e9 } };
  research.setTickerFetcher(async () => tickers);
  rwa.setTickerFetcher(async () => tickers);
  dexLib.setTickerFetcher(async () => tickers);
  dexLib.setMidsFetcher(async () => ({}));
  safety.setPairSearcher(async () => null);
  try {
    const d = await research.buildDossier('ETH');
    assert.ok(d, 'dossier still builds');
    const sec = d.sections.find(s => s.title === 'Safety read');
    assert.ok(sec, 'safety section present in degraded (CEX-only) form');
    assert.ok(d.safety.notes.some(n => /did not run/.test(n)));
  } finally {
    research.setTickerFetcher(null);
    rwa.setTickerFetcher(null);
    dexLib.setTickerFetcher(null);
    dexLib.setMidsFetcher(null);
    safety.setPairSearcher(null);
  }
});
