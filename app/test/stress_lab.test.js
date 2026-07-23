'use strict';
/**
 * Portfolio Stress Lab (Digital Twin). A pure, deterministic simulation model
 * (public/js/stress-model.js) + the /stress page. §4: percent-only what-if on a
 * hypothetical portfolio — no account, no dollars, no advice.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const M = require('../public/js/stress-model');

test('classify buckets assets into major / alt / stable', () => {
  assert.equal(M.classify('BTC'), 'major');
  assert.equal(M.classify('ethusdt'), 'major');
  assert.equal(M.classify('SOL'), 'alt');
  assert.equal(M.classify('USDC'), 'stable');
  assert.equal(M.classify('DAI'), 'stable');
});

test('spot (1x) long survives a −30% shock; a 5x long liquidates on −25%', () => {
  const spot = M.simulate([{ asset: 'BTC', weight: 100, leverage: 1, dir: 'long' }], { major: -30, alt: -30, stable: 0 });
  assert.equal(spot.legs[0].liquidated, false);
  assert.ok(Math.abs(spot.drawdownPct - (-30)) < 1e-9);            // −30% of equity

  const lev = M.simulate([{ asset: 'SOL', weight: 100, leverage: 5, dir: 'long' }], { major: 0, alt: -25, stable: 0 });
  assert.equal(lev.legs[0].liquidated, true);                      // 5x × −25% = −125% → wiped
  assert.ok(Math.abs(lev.drawdownPct - (-100)) < 1e-9);           // capped at −100% of margin
});

test('a short profits when price falls (positive contribution)', () => {
  const r = M.simulate([{ asset: 'BTC', weight: 50, leverage: 2, dir: 'short' }], { major: -20, alt: -20, stable: 0 });
  assert.ok(r.legs[0].contributionPct > 0, 'short gains in a drop');
  assert.ok(r.drawdownPct > 0);
});

test('a depeg scenario hits stablecoin cash and holdings', () => {
  const r = M.simulate([{ asset: 'USDC', weight: 60, leverage: 1, dir: 'long' }], { major: 0, alt: 0, stable: -7 });
  // 60% in USDC at −7% plus 40% cash also in stables at −7% → ≈ −7% overall.
  assert.ok(r.drawdownPct < -6 && r.drawdownPct > -8);
});

test('runAll returns the five built-in scenarios; leverage raises liquidations', () => {
  const runs = M.runAll([
    { asset: 'BTC', weight: 40, leverage: 3, dir: 'long' },
    { asset: 'SOL', weight: 40, leverage: 10, dir: 'long' },
    { asset: 'USDC', weight: 20, leverage: 1, dir: 'long' },
  ]);
  assert.equal(runs.length, 5);
  const swan = runs.find(x => x.scenario.id === 'black_swan').result;
  assert.ok(swan.liquidatedCount >= 2);
  assert.equal(swan.severity, 'critical');
  // every drawdown is a finite percent — no dollar concept in the model at all
  assert.ok(!/\$/.test(JSON.stringify(runs)));
});

test('the /stress page + route + model + nav are wired', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.get\('\/stress'/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'stress.html'), 'utf8');
  assert.match(html, /js\/stress-model\.js/);
  assert.match(html, /StressModel/);
  assert.match(html, /digital twin/i);
  assert.match(html, /not investment advice/i);          // §4 disclaimer
  assert.ok(!/\$[0-9]|fmtMoney/.test(html), 'no dollar amounts on the stress page');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/stress"/);
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  assert.match(i18n, /'nav\.stress'/);
});
