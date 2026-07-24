'use strict';
/**
 * Season ceremony watch — the starting gun and final whistle announce
 * themselves exactly once (durable flags survive restarts), crown the
 * champion by handle, and stay §4-clean (no numbers in the fanfare).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const { transitions, runOnce } = require('../lib/season_watch');
const { pool } = require('../db');

test('transitions: due ceremonies only, flags silence them', () => {
  const now = new Date('2026-08-15T00:00:00Z');
  const seasons = [
    { id: 1, starts_at: '2026-08-01', ends_at: '2026-09-01', announced_live: 0, announced_end: 0 }, // live, gun due
    { id: 2, starts_at: '2026-08-01', ends_at: '2026-09-01', announced_live: 1, announced_end: 0 }, // already announced
    { id: 3, starts_at: '2026-07-01', ends_at: '2026-08-01', announced_live: 1, announced_end: 0 }, // ended, whistle due
    { id: 4, starts_at: '2026-09-01', ends_at: '2026-10-01', announced_live: 0, announced_end: 0 }, // upcoming, nothing
    { id: 5, starts_at: '2026-07-01', ends_at: '2026-08-01', announced_live: 0, announced_end: 0 }, // ended, BOTH due
  ];
  const due = transitions(seasons, now);
  const keys = due.map((d) => `${d.season.id}:${d.kind}`).sort();
  assert.deepEqual(keys, ['1:live', '3:ended', '5:ended', '5:live']);
});

test('runOnce announces once, flips the durable flag, crowns by handle, no numbers (§4)', async () => {
  const push = require('../lib/push');
  push.setSender(async () => {});
  // an already-ended season with an in-window champion trade
  await pool.execute(
    'INSERT INTO arena_seasons (name, starts_at, ends_at, created_at) VALUES (?, ?, ?, ?)',
    ['Whistle Cup', new Date(Date.now() - 5 * 86400000), new Date(Date.now() - 3600000), new Date()]);
  const [rows] = await pool.execute('SELECT id, name FROM arena_seasons', []);
  const cup = rows.find((s) => s.name === 'Whistle Cup');
  // champion: a handled user with an in-window close
  const bcrypt = null; // register through the store directly
  pool.users.push({ id: 9901, email: 'champ@example.com', password_hash: 'x', plan: 'free', leaderboard_handle: 'whistle_champ' });
  await pool.execute(
    'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
    [9901, 'BTCUSDT', 'LONG', 100, 110, 1000, 5, 500, 'manual', new Date(Date.now() - 2 * 86400000), new Date(Date.now() - 2 * 3600000)]);
  await runOnce(new Date());
  const [events] = await pool.execute('SELECT event_type, title, body FROM agent_events ORDER BY id DESC LIMIT 8', []);
  const whistle = events.find((e) => /Whistle Cup — final whistle/.test(e.title));
  assert.ok(whistle, 'final whistle announced');
  assert.match(whistle.body, /whistle_champ/);
  assert.ok(!/\$\s?\d|\d+%/.test(whistle.title + ' ' + whistle.body), 'ceremony carries no numbers');
  // second run: flag flipped → silent
  const before = events.length;
  await runOnce(new Date());
  const [events2] = await pool.execute('SELECT title FROM agent_events ORDER BY id DESC LIMIT 12', []);
  const whistles = events2.filter((e) => /Whistle Cup — final whistle/.test(e.title));
  assert.equal(whistles.length, 1, 'the ceremony never replays');
  push.setSender(null);
});

test('the watch boots with the server', () => {
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /startSeasonWatch\(\)/);
});

test('Genesis everywhere — landing ribbon + arena SSR unfurl carry the live season', () => {
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  // landing ribbon: fetches the public season API, count-only extras (§4)
  assert.match(index, /id="seasonRibbon"/);
  assert.match(index, /api\/arena\/season/);
  assert.match(index, /competing/);
  assert.ok(!/\$\s?\d/.test(index.slice(index.indexOf('seasonRibbon'), index.indexOf('seasonRibbon') + 900)));
  // arena unfurl: tokenized og tags + SSR injection with honest fallback
  assert.match(arena, /__ARENATITLE__/);
  assert.match(arena, /__ARENAOG__/);
  assert.match(srv, /__ARENAOG__/);
  assert.match(srv, /is LIVE until/);
  assert.match(srv, /Hall of Champions/);
});
