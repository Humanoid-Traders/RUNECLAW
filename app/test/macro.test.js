'use strict';
/**
 * Macro AI assembly — the pure blend of Fear & Greed + global market structure
 * + the engine's BTC regime into one risk-on/off read and a plain-language
 * brief. Network is never touched here: assembleMacro() is a pure function of
 * its inputs, so these tests pin the bands, the re-weighting when a source is
 * missing, and the brief content.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { assembleMacro, classifyBand } = require('../routes/macro');

const GLOBAL = { mcap_usd: 2.29e12, vol_usd: 3.7e10, btc_dom: 56.5, eth_dom: 9.8, mcap_chg_24h: 0.66 };

test('derives others-dominance and market-structure label', () => {
  const btcLed = assembleMacro({ global: { ...GLOBAL, btc_dom: 60, eth_dom: 9 }, fng: { value: '50', classification: 'Neutral' } });
  assert.equal(btcLed.others_dominance, 31);       // 100 - 60 - 9
  assert.equal(btcLed.structure, 'BTC-led');
  assert.match(btcLed.brief, /concentrated in BTC/);
  const alt = assembleMacro({ global: { ...GLOBAL, btc_dom: 44, eth_dom: 18 }, fng: { value: '60', classification: 'Greed' } });
  assert.equal(alt.structure, 'Alt-heavy');
  assert.match(alt.brief, /rotating into alts/);
  const broad = assembleMacro({ global: { ...GLOBAL, btc_dom: 51, eth_dom: 12 }, fng: { value: '50', classification: 'Neutral' } });
  assert.equal(broad.structure, 'Broad');
  // No dominance data -> no structure, no others.
  const none = assembleMacro({ fng: { value: '50', classification: 'Neutral' } });
  assert.equal(none.structure, null);
  assert.equal(none.others_dominance, null);
});

test('bands map risk score to the right label', () => {
  assert.equal(classifyBand(10).key, 'risk_off');
  assert.equal(classifyBand(30).key, 'cautious');
  assert.equal(classifyBand(50).key, 'neutral');
  assert.equal(classifyBand(70).key, 'risk_on');
  assert.equal(classifyBand(90).key, 'euphoric');
});

test('extreme fear + falling cap + bearish regime reads risk-off', () => {
  const m = assembleMacro({
    global: { ...GLOBAL, mcap_chg_24h: -5 },
    fng: { value: '12', classification: 'Extreme Fear', previous: '18' },
    regime: { label: 'BEARISH', score: -0.8 },
  });
  assert.ok(m.risk_score < 30, `expected risk-off, got ${m.risk_score}`);
  assert.equal(m.band.key, 'risk_off');
  assert.equal(m.fear_greed.value, 12);
  assert.equal(m.btc_dominance, 56.5);
  assert.match(m.brief, /RISK-OFF/);
  assert.match(m.brief, /Extreme Fear/);
  assert.match(m.brief, /down 6 from yesterday/);      // F&G delta 12-18
  assert.match(m.brief, /BEARISH/);
});

test('greed + rising cap + bullish regime reads risk-on', () => {
  const m = assembleMacro({
    global: { ...GLOBAL, mcap_chg_24h: 4 },
    fng: { value: '78', classification: 'Extreme Greed', previous: '70' },
    regime: { label: 'BULLISH', score: 0.7 },
  });
  assert.ok(m.risk_score >= 70, `expected risk-on, got ${m.risk_score}`);
  assert.ok(['risk_on', 'euphoric'].includes(m.band.key));
  assert.match(m.brief, /up 8 from yesterday/);
});

test('missing sources are omitted and weights renormalise', () => {
  // Only Fear & Greed present -> score equals the F&G value, band derived.
  const m = assembleMacro({ fng: { value: '50', classification: 'Neutral', previous: null } });
  assert.equal(m.risk_score, 50);
  assert.equal(m.band.key, 'neutral');
  assert.equal(m.market_cap_usd, null);
  assert.deepEqual(m.sources, ['fear_greed']);
  assert.equal(m.regime, null);
  assert.doesNotMatch(m.brief, /yesterday/); // no previous -> no delta clause
});

test('no data at all -> null score, empty sources (route turns this into 502)', () => {
  const m = assembleMacro({});
  assert.equal(m.risk_score, null);
  assert.equal(m.band, null);
  assert.equal(m.sources.length, 0);
});

test('values are coerced and clamped; garbage is dropped', () => {
  const m = assembleMacro({
    global: { mcap_usd: 'not-a-number', btc_dom: 60, mcap_chg_24h: 1 },
    fng: { value: '140', classification: 'Extreme Greed', previous: '-5' }, // out of range
  });
  assert.equal(m.market_cap_usd, null);       // non-numeric dropped
  assert.equal(m.btc_dominance, 60);
  assert.equal(m.fear_greed.value, 100);      // clamped to 0..100
  assert.equal(m.fear_greed.previous, 0);     // clamped
});
