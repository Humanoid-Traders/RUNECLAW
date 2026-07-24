'use strict';
/**
 * Systemic Risk Sentinel (lib/sentinel.js + /api/market/sentinel + /sentinel).
 * A market-wide crowding / herding read from PUBLIC data (funding, OI, ΔOI, 24h
 * direction). §4: public market facts + heuristic FLAGS, never a verdict and
 * never a user's account/P&L. Market OI in dollars is a public fact and allowed.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { buildSentinel, FUND_HOT_BPS, DOI_SURGE_PCT } = require('../lib/sentinel');

const AT = 1_800_000_000_000;

function universe(over) {
  // 40 coins; overrides let each test dial one axis of crowding.
  const coins = [];
  for (let i = 0; i < 40; i++) {
    coins.push(Object.assign({
      base: 'C' + i, symbol: 'C' + i + 'USDT',
      funding: 0, oi_usd: 1e8 * (40 - i), doi_pct: 0, dir: 0, change_pct: (i % 2 ? 1 : -1),
    }, typeof over === 'function' ? over(i) : over));
  }
  return coins;
}

test('a balanced, quiet market reads calm with no crowding flags', () => {
  const s = buildSentinel(universe(), AT);
  assert.equal(s.universe, 40);
  assert.ok(s.gauge.score < 25);
  assert.equal(s.gauge.level, 'calm');
  assert.equal(s.flags[0].kind, 'calm');
});

test('crowded-long funding + herding + leverage surge reads high with flags', () => {
  const s = buildSentinel(universe(function (i) {
    return { funding: 0.0007, dir: 0.4, change_pct: -3, doi_pct: i < 12 ? 20 : 1 };
  }), AT);
  assert.equal(s.gauge.level, 'high');
  assert.ok(s.gauge.score >= 75);
  const kinds = s.flags.map(f => f.kind);
  assert.ok(kinds.includes('funding') && kinds.includes('herding') && kinds.includes('leverage') && kinds.includes('bias'));
  assert.ok(s.funding.avg_bps >= FUND_HOT_BPS);            // crowded long
  assert.ok(s.funding.crowded_long.length > 0);
  assert.equal(s.funding.crowded_short.length, 0);
  assert.ok(s.leverage.surging.length > 0);
  assert.ok(s.leverage.surging[0].doi_pct >= DOI_SURGE_PCT);
  assert.equal(s.herding.direction, 'down');
  assert.equal(s.bias.label, 'crowded long');
});

test('negative funding surfaces crowded-short (squeeze-up) books', () => {
  const s = buildSentinel(universe({ funding: -0.0008 }), AT);
  assert.ok(s.funding.avg_bps <= -FUND_HOT_BPS);
  assert.ok(s.funding.crowded_short.length > 0);
  assert.equal(s.funding.crowded_long.length, 0);
});

test('empty / no-OI universe is handled, not crashed', () => {
  assert.equal(buildSentinel([], AT).universe, 0);
  assert.equal(buildSentinel([{ base: 'X', oi_usd: 0 }], AT).universe, 0);
});

test('§4: the payload carries market facts + heuristic flags, no user account/P&L', () => {
  const raw = JSON.stringify(buildSentinel(universe({ funding: 0.0007 }), AT)).toLowerCase();
  for (const forbidden of ['equity', 'net_pnl', 'pnl_usd', 'wallet_address', 'user_id']) {
    assert.ok(!raw.includes(forbidden), `no "${forbidden}" on the systemic surface`);
  }
  assert.ok(raw.includes('heuristic'));                    // labelled a read, not a verdict
});

test('the /api/market/sentinel endpoint + /sentinel page + nav are wired', () => {
  const market = fs.readFileSync(path.join(__dirname, '..', 'routes', 'market.js'), 'utf8');
  assert.match(market, /router\.get\('\/sentinel'/);
  assert.match(market, /buildSentinel/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.get\('\/sentinel'/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'sentinel.html'), 'utf8');
  assert.match(html, /\/api\/market\/sentinel/);
  assert.match(html, /not a verdict|heuristic/i);
  assert.match(html, /not investment advice/i);
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/sentinel"/);
});
