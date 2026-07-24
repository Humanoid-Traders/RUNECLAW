'use strict';
/**
 * Arena achievements — honest gamification: every badge derives from
 * verifiable arena facts (counts, streaks, percents), never a dollar figure,
 * so earned badges are §4-safe on any surface. Locked badges show what's left.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { computeArenaBadges } = require('../lib/arena_badges');

const W = (sym) => ({ pnl: 100, reason: 'manual', symbol: sym || 'BTCUSDT' });
const L = (liq) => ({ pnl: -100, reason: liq ? 'liquidated' : 'manual', symbol: 'BTCUSDT' });
const earned = (rows, key) => rows.find((b) => b.key === key).earned;

test('a fresh account has everything locked, listed', () => {
  const rows = computeArenaBadges({ trades: [], returnPct: 0 });
  assert.equal(rows.length, 11);
  assert.ok(rows.every((b) => !b.earned));
  assert.ok(rows.every((b) => b.icon && b.name && b.desc));
});

test('first close, streaks and comeback are detected in time order', () => {
  // rows arrive NEWEST first: [W, W, W, L, L, W] chrono = W L L W W W
  const rows = computeArenaBadges({ trades: [W(), W(), W(), L(), L(), W()], returnPct: 1 });
  assert.ok(earned(rows, 'first_blood'));
  assert.ok(earned(rows, 'hot_streak'), '3 consecutive wins at the end');
  assert.ok(earned(rows, 'comeback'), 'a win right after two straight losses');
  assert.ok(earned(rows, 'in_the_green'));
});

test('sharpshooter and iron hands need 10+ closes; liquidation breaks iron hands', () => {
  const ten = [W(), W(), W(), W(), W(), W(), W(), L(), L(), L()];   // 70% win rate
  let rows = computeArenaBadges({ trades: ten, returnPct: 4 });
  assert.ok(earned(rows, 'veteran'));
  assert.ok(earned(rows, 'sharpshooter'));
  assert.ok(earned(rows, 'iron_hands'));
  rows = computeArenaBadges({ trades: ten.slice(0, 9).concat([L(true)]), returnPct: 4 });
  assert.ok(!earned(rows, 'iron_hands'), 'a liquidation forfeits iron hands');
});

test('explorer and high flyer', () => {
  const rows = computeArenaBadges({ trades: [W('BTCUSDT'), W('ETHUSDT'), W('SOLUSDT')], returnPct: 12 });
  assert.ok(earned(rows, 'explorer'));
  assert.ok(earned(rows, 'high_flyer'));
});

test('§4: badge payload carries counts/percents only — no dollar text', () => {
  const blob = JSON.stringify(computeArenaBadges({ trades: [W()], returnPct: 3 }));
  assert.ok(!/\$\s?\d|usd|balance|equity/i.test(blob));
});

test('the account endpoint + page ship the badges', () => {
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(route, /computeArenaBadges/);
  assert.match(html, /id="badges"/);
  assert.match(html, /badge-chip/);
  assert.match(html, /locked/);
});

test('the arena celebrates new badges and guides first-run users', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="cheer"/);
  assert.match(html, /Achievement unlocked/);
  assert.match(html, /rc_arena_badges/);           // client-side diff store
  assert.match(html, /prefers-reduced-motion/);
  assert.match(html, /id="starter"/);
  assert.match(html, /Your first 5 minutes/);
  assert.match(html, /Practice-follow/);
});

test('purposeful motion: count-up stats, pnl change pulses, confetti — all reduced-motion aware', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /RC\.countUp\(\$\('vBal'\)/);
  assert.match(html, /RC\.countUp\(\$\('vRet'\)/);
  assert.match(html, /pulse-up/);
  assert.match(html, /pulseCls\(/);
  assert.match(html, /function confetti/);
  // every motion path respects prefers-reduced-motion (countUp internally + CSS + confetti guard)
  const guards = (html.match(/prefers-reduced-motion/g) || []).length;
  assert.ok(guards >= 3, `reduced-motion guards across cheer/pulse/confetti (found ${guards})`);
});
