'use strict';
/**
 * Strength Map scoring — factor scores + composite long/short strength from
 * PUBLIC Bitget market data only. No account/P&L data ever enters this (§4).
 */
const test = require('node:test');
const assert = require('node:assert');
const { buildStrengthMap, scoreTicker, WEIGHTS } = require('../lib/strengthmap');

function tk(o) {
  return Object.assign({
    symbol: 'AAAUSDT', lastPr: '100', change24h: '0', high24h: '110', low24h: '90',
    fundingRate: '0', holdingAmount: '1000', usdtVolume: '1000000',
  }, o);
}

test('a strong up-mover near its high scores LONG > SHORT', () => {
  const s = scoreTicker(tk({ change24h: '0.12', lastPr: '109', high24h: '110', low24h: '90' }));
  assert.ok(s.long_score > s.short_score, `long ${s.long_score} should beat short ${s.short_score}`);
  assert.ok(s.factors.momentum > 0 && s.factors.trend > 0);
  assert.equal(s.base, 'AAA');
});

test('a sharp down-mover near its low scores SHORT > LONG', () => {
  const s = scoreTicker(tk({ change24h: '-0.10', lastPr: '91', high24h: '110', low24h: '90' }));
  assert.ok(s.short_score > s.long_score);
  assert.ok(s.factors.momentum < 0 && s.factors.trend < 0);
});

test('positive funding is a contrarian (negative) factor — crowded longs', () => {
  const hot = scoreTicker(tk({ fundingRate: '0.001' }));   // very positive funding
  assert.ok(hot.factors.funding < 0, 'crowded longs → funding headwind');
  const cold = scoreTicker(tk({ fundingRate: '-0.001' }));
  assert.ok(cold.factors.funding > 0);
});

test('OI is reported in USD (base * price) and ΔOI needs a prior snapshot', () => {
  const first = scoreTicker(tk({ holdingAmount: '1000', lastPr: '2' }));
  assert.equal(first.oi_usd, 2000);
  assert.equal(first.doi_pct, 0, 'no history → ΔOI 0');
  const grown = scoreTicker(tk({ holdingAmount: '1500', lastPr: '2' }), 2000);
  assert.equal(grown.doi_pct, 50, 'OI 2000→3000 = +50%');
  assert.ok(grown.factors.doi > 0);
});

test('scores are bounded and every factor is in [-1, 1]', () => {
  const s = scoreTicker(tk({ change24h: '5', fundingRate: '0.05' })); // absurd inputs
  assert.ok(s.long_score >= 0 && s.long_score <= 100);
  assert.ok(s.short_score >= 0 && s.short_score <= 100);
  for (const v of Object.values(s.factors)) assert.ok(v >= -1 && v <= 1, `factor ${v} out of range`);
});

test('scoreTicker rejects junk (no symbol / zero price)', () => {
  assert.equal(scoreTicker(null), null);
  assert.equal(scoreTicker(tk({ lastPr: '0' })), null);
  assert.equal(scoreTicker({ symbol: 'X' }), null); // price 0
});

test('buildStrengthMap sorts by volume, applies the limit, and emits an OI snapshot', () => {
  const tickers = [
    tk({ symbol: 'LOWUSDT', usdtVolume: '10' }),
    tk({ symbol: 'HIGHUSDT', usdtVolume: '9000000', holdingAmount: '500', lastPr: '4' }),
    tk({ symbol: 'MIDUSDT', usdtVolume: '5000' }),
    tk({ symbol: 'DEADUSDT', usdtVolume: '0' }),   // dropped (no volume)
  ];
  const { coins, oiSnapshot, count } = buildStrengthMap(tickers, null, 2);
  assert.equal(count, 2);
  assert.deepEqual(coins.map(c => c.symbol), ['HIGHUSDT', 'MIDUSDT']);
  assert.equal(oiSnapshot['HIGHUSDT'], 2000); // 500 * 4 — snapshot covers all scored, not just top-N
  assert.ok(!('DEADUSDT' in oiSnapshot), 'zero-volume dropped');
});

test('composite weights sum to 1 (documented blend)', () => {
  const sum = Object.values(WEIGHTS).reduce((a, b) => a + b, 0);
  assert.ok(Math.abs(sum - 1) < 1e-9, `weights sum ${sum}`);
});
