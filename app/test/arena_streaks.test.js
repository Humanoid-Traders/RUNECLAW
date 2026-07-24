'use strict';
/**
 * Streaks & weekly quests — gamification derived ONLY from real closed-trade
 * facts; nothing stored or granted, everything recomputes from history.
 * §4: counts only; streak_days (a count) is the only public addition.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { computeStreak, weeklyQuests, weekStart, QUEST_POOL } = require('../lib/arena_streaks');

const DAY = 86400000;
const NOW = new Date('2026-07-24T15:00:00Z');
const daysAgo = (n) => new Date(NOW.getTime() - n * DAY);
const T = (closedDaysAgo, extra) => Object.assign(
  { symbol: 'BTCUSDT', pnl: 10, reason: 'manual', closed_at: daysAgo(closedDaysAgo) }, extra);

test('streak: consecutive UTC close-days, with the yesterday grace rule', () => {
  // Closes today, yesterday, 2 days ago → current 3, active today.
  let s = computeStreak([T(0), T(1), T(2)], NOW);
  assert.deepEqual(s, { current: 3, best: 3, active_today: true });
  // Last close YESTERDAY: streak survives (today isn't over) but isn't active.
  s = computeStreak([T(1), T(2)], NOW);
  assert.equal(s.current, 2);
  assert.equal(s.active_today, false);
  // Last close 2 days ago: streak is broken.
  s = computeStreak([T(2), T(3)], NOW);
  assert.equal(s.current, 0);
  assert.equal(s.best, 2);
  // Gaps split runs; best is the longest anywhere.
  s = computeStreak([T(0), T(4), T(5), T(6), T(7)], NOW);
  assert.equal(s.current, 1);
  assert.equal(s.best, 4);
  // No trades → all zero.
  assert.deepEqual(computeStreak([], NOW), { current: 0, best: 0, active_today: false });
});

test('quests: deterministic weekly rotation, three distinct quests', () => {
  const q1 = weeklyQuests([], NOW);
  const q2 = weeklyQuests([], NOW);
  assert.equal(q1.length, 3);
  assert.deepEqual(q1.map((q) => q.key), q2.map((q) => q.key), 'same week → same quests');
  assert.equal(new Set(q1.map((q) => q.key)).size, 3, 'three distinct quests');
  // A different week rotates to a different set.
  const other = weeklyQuests([], new Date(NOW.getTime() + 7 * DAY));
  assert.notDeepEqual(q1.map((q) => q.key), other.map((q) => q.key));
});

test('quests: progress counts ONLY this ISO week\'s closes', () => {
  const ws = weekStart(NOW).getTime();
  const inWeek = { symbol: 'ETHUSDT', pnl: 5, reason: 'tp', closed_at: new Date(ws + DAY) };
  const before = { symbol: 'ETHUSDT', pnl: 5, reason: 'tp', closed_at: new Date(ws - DAY) };
  const withOld = weeklyQuests([before], NOW);
  assert.ok(withOld.every((q) => q.have === 0), 'last week\'s closes count for nothing');
  const withNew = weeklyQuests([inWeek], NOW);
  assert.ok(withNew.some((q) => q.have > 0), 'this week\'s close registers somewhere');
});

test('quest pool definitions are honest counters', () => {
  const week = [
    { symbol: 'A', pnl: 5, reason: 'tp' }, { symbol: 'B', pnl: -2, reason: 'sl' },
    { symbol: 'A', pnl: 3, reason: 'manual' },
  ];
  const by = Object.fromEntries(QUEST_POOL.map((q) => [q.key, q.have(week)]));
  assert.equal(by.five_closes, 3);
  assert.equal(by.three_tp, 1);
  assert.equal(by.three_symbols, 2);
  assert.equal(by.three_wins, 2);
  assert.equal(by.survive_zero, 1);        // 3 closes, none liquidated
  assert.equal(by.planned_exit, 2);        // tp + sl
});

test('wiring: account payload, public card count, page render + celebration', () => {
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'arena.js'), 'utf8');
  assert.match(route, /streak: streaks\.computeStreak\(allTrades\)/);
  assert.match(route, /quests: streaks\.weeklyQuests\(allTrades\)/);
  const card = fs.readFileSync(path.join(__dirname, '..', 'lib', 'arena_trader.js'), 'utf8');
  assert.match(card, /streak_days/);
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(arena, /-day streak/);
  assert.match(arena, /Weekly quests · reset Monday 00:00 UTC/);
  assert.match(arena, /function celebrateQuests\(/);
  const trader = fs.readFileSync(path.join(__dirname, '..', 'public', 'trader.html'), 'utf8');
  assert.match(trader, /streak_days > 0/);
});
