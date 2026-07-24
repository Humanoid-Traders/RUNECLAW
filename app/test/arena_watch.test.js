'use strict';
/**
 * Arena liquidation watch — one push when a paper position drifts within
 * WARN_AT of its liq price, re-armed only past REARM (hysteresis: a hovering
 * market never spams). §4: owner-only push, symbol/direction/percent payload.
 * The decision core is pure, so every transition is exact.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { evaluate, proximity, WARN_AT, REARM } = require('../lib/arena_watch');
const { liqPrice } = require('../lib/arena');

const POS = { id: 1, user_id: 9, symbol: 'BTCUSDT', direction: 'LONG', entry: 100, margin: 100, leverage: 10 };
const mk = (price) => ({ BTCUSDT: { price } });

test('proximity agrees with the engine liq price, both directions', () => {
  const lp = liqPrice(POS);
  assert.ok(Math.abs(proximity(POS, lp)) < 1e-9, 'zero at the liq price');
  assert.ok(proximity(POS, 100) > WARN_AT, 'safe at entry');
  const short = { ...POS, direction: 'SHORT' };
  assert.ok(proximity(short, 100) > WARN_AT);
  assert.ok(proximity(short, liqPrice(short)) < 1e-9);
});

test('warn → silence → re-arm hysteresis', () => {
  const lp = liqPrice(POS);                       // ≈ 90.05 for 10x long
  const hot = lp * 1.02;                          // within 3%
  let r = evaluate([POS], mk(hot), new Set());
  assert.equal(r.notify.length, 1, 'first approach warns');
  r = evaluate([POS], mk(hot), r.warned);
  assert.equal(r.notify.length, 0, 'hovering stays silent');
  const lukewarm = lp * 1.05;                     // 5%: above WARN, below REARM
  r = evaluate([POS], mk(lukewarm), r.warned);
  assert.equal(r.notify.length, 0);
  assert.ok(r.warned.has(1), 'not yet re-armed at 5%');
  const safe = lp * 1.08;                         // past 6% → re-arm
  r = evaluate([POS], mk(safe), r.warned);
  assert.ok(!r.warned.has(1), 're-armed after recovery');
  r = evaluate([POS], mk(hot), r.warned);
  assert.equal(r.notify.length, 1, 'a second approach warns again');
});

test('a crossed liquidation still warns once (last call), missing marks are inert', () => {
  const lp = liqPrice(POS);
  let r = evaluate([POS], mk(lp * 0.98), new Set());
  assert.equal(r.notify.length, 1);
  assert.ok(r.notify[0].prox < 0);
  r = evaluate([POS], {}, r.warned);              // feed vanishes → no verdicts, stays silenced
  assert.equal(r.notify.length, 0);
  assert.ok(r.warned.has(1));
});

test('the watch is §4-clean and boots with the server', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'arena_watch.js'), 'utf8');
  assert.match(src, /notifySubscribers/);
  assert.match(src, /\[p\.user_id\]/);            // owner-only targeting
  assert.ok(!/\$\{[^}]*margin|\$\{[^}]*balance/.test(src), 'push body carries no amounts');
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /startArenaWatch\(\)/);
});
