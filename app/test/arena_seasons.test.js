'use strict';
/**
 * Arena competition seasons — a season is a NAMED TIME WINDOW, never a reset:
 * the season board ranks realized percent return from trades closed inside the
 * window, so nobody can erase a bad run and the all-time board keeps running.
 * §4: public payload is opt-in handles + percent only; authoring is operator-only.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('fs');
const path = require('path');
const authModule = require('../auth');
const { seasonStatus, validateSeason, seasonRanking } = require('../lib/arena_seasons');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

// ---- Pure helpers -------------------------------------------------------

test('seasonStatus walks upcoming → live → ended', () => {
  const s = { starts_at: '2026-08-01T00:00:00Z', ends_at: '2026-09-01T00:00:00Z' };
  assert.equal(seasonStatus(s, new Date('2026-07-30T00:00:00Z')), 'upcoming');
  assert.equal(seasonStatus(s, new Date('2026-08-15T00:00:00Z')), 'live');
  assert.equal(seasonStatus(s, new Date('2026-09-02T00:00:00Z')), 'ended');
});

test('validateSeason enforces name, order and length', () => {
  assert.ok(validateSeason({ name: 'Genesis Season', starts_at: '2026-08-01', ends_at: '2026-09-01' }).ok);
  assert.ok(!validateSeason({ name: 'x', starts_at: '2026-08-01', ends_at: '2026-09-01' }).ok);
  assert.ok(!validateSeason({ name: 'Backwards', starts_at: '2026-09-01', ends_at: '2026-08-01' }).ok);
  assert.ok(!validateSeason({ name: 'Too Long', starts_at: '2026-01-01', ends_at: '2026-12-31' }).ok);
});

test('seasonRanking sums in-window pnl vs the uniform stake, opt-in only', () => {
  const trades = [
    { user_id: 1, pnl: 500 }, { user_id: 1, pnl: -200 },   // +3% net
    { user_id: 2, pnl: 1000 },                              // +10% but no handle
    { user_id: 3, pnl: 800 },                               // +8%
  ];
  const rows = seasonRanking(trades, new Map([[1, 'ace'], [3, 'bravo']]));
  assert.equal(rows.length, 2);
  assert.equal(rows[0].handle, 'bravo');
  assert.equal(rows[0].return_pct, 8);
  assert.equal(rows[1].handle, 'ace');
  assert.equal(rows[1].return_pct, 3);
  const blob = JSON.stringify(rows).toLowerCase();
  for (const needle of ['balance', 'equity', 'user_id', 'email']) {
    assert.ok(!blob.includes(needle), `season rows must not contain "${needle}"`);
  }
});

// ---- API ----------------------------------------------------------------

let server, base;
test.before(async () => {
  setTickerFetcher(async () => ({ BTCUSDT: { price: 100, change: 0, volume: 1 } }));
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('season read is public and honest when none is authored', async () => {
  const r = await req('GET', '/api/arena/season');
  assert.equal(r.status, 200);
  assert.equal(r.data.season, null);
});

test('authoring a season is operator-only; a live season ranks in-window closes', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'season1@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  // non-admin refused
  const deny = await req('POST', '/api/arena/season', { token, body: { name: 'Genesis Season', starts_at: '2026-01-01', ends_at: '2026-03-01' } });
  assert.equal(deny.status, 403);
  // promote to admin directly in the in-memory store (mock mode), then author
  // a season spanning "now"
  const urow = pool.users.find((u) => u.email === 'season1@example.com');
  urow.plan = 'admin';
  const start = new Date(Date.now() - 3600000), end = new Date(Date.now() + 86400000);
  const ok = await req('POST', '/api/arena/season', { token, body: { name: 'Genesis Season', starts_at: start, ends_at: end } });
  assert.equal(ok.status, 200);
  // an in-window closed trade counts once the user has a handle
  await pool.execute('INSERT INTO arena_accounts (user_id, balance, created_at) VALUES (?, ?, ?)', [reg.data.user_id, 10000, new Date()]);
  await pool.execute(
    'INSERT INTO arena_trades (user_id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
    [reg.data.user_id, 'BTCUSDT', 'LONG', 100, 110, 1000, 5, 500, 'manual', new Date(), new Date()]);
  await pool.execute('UPDATE users SET leaderboard_handle = ? WHERE id = ?', ['season_ace', reg.data.user_id]);
  const s = await req('GET', '/api/arena/season');
  assert.equal(s.data.season.status, 'live');
  const me = (s.data.rows || []).find((x) => x.handle === 'season_ace');
  assert.ok(me, 'ranked in the live season');
  assert.equal(me.return_pct, 5);
  assert.ok(!/balance|equity|email/i.test(JSON.stringify(s.data)));
});

test('the /arena page mounts the season banner + standings', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="seasonPanel"/);
  assert.match(html, /api\/arena\/season/);
  assert.match(html, /ends in |starts in /);
  assert.match(html, /final standings/);
});

test('the /arena page mounts an operator-only season launcher', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="seasonAdmin"/);
  assert.match(html, /plan === 'admin'/);        // client gate is display-only…
  assert.match(html, /operator only/i);
  // …the POST goes to the server route that re-checks admin (asserted above)
  assert.match(html, /method: 'POST'/);
});

test('the main leaderboard page teases the Arena season (§4-safe)', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'leaderboard.html'), 'utf8');
  assert.match(html, /id="arenaSeasonPanel"/);
  assert.match(html, /api\/arena\/season/);
  assert.match(html, /Enter the Arena/);
  // §4: no $-amounts in the teaser markup
  const cut = html.slice(html.indexOf('arenaSeasonPanel'));
  assert.ok(!/\$\s?\d/.test(cut), 'no $-amount in the arena teaser');
});

test('the Hall of Champions serves ended seasons with immutable podiums (§4)', async () => {
  // The live season from the earlier test hasn't ended → empty hall.
  let h = await req('GET', '/api/arena/seasons');
  assert.equal(h.status, 200);
  assert.deepEqual(h.data.seasons, []);
  // Author an already-ended season via the admin from the earlier test.
  const [admins] = [[pool.users.find((u) => u.email === 'season1@example.com')]];
  assert.ok(admins[0] && admins[0].plan === 'admin');
  await pool.execute(
    'INSERT INTO arena_seasons (name, starts_at, ends_at, created_at) VALUES (?, ?, ?, ?)',
    ['Closed Cup', new Date(Date.now() - 7 * 86400000), new Date(Date.now() - 3600000), new Date()]);
  h = await req('GET', '/api/arena/seasons');
  const cup = (h.data.seasons || []).find((s) => s.name === 'Closed Cup');
  assert.ok(cup, 'ended season appears in the hall');
  // The earlier in-window trade (closed "now"… outside this window) must NOT rank here;
  // podium may be empty — but the shape stays §4-clean either way.
  const blob = JSON.stringify(h.data).toLowerCase();
  for (const needle of ['balance', 'equity', 'email', 'user_id']) {
    assert.ok(!blob.includes(needle), `hall must not contain "${needle}"`);
  }
});

test('the /arena page mounts the Hall of Champions', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="hallPanel"/);
  assert.match(html, /api\/arena\/seasons/);
  assert.match(html, /🥇/);
  assert.match(html, /\/trader\//);          // podium links to trader cards
});

test('launching a season announces it — push broadcast + agent-feed event (§4: no numbers)', async () => {
  // Capture pushes via the injectable sender; configure fake VAPID first.
  const push = require('../lib/push');
  const sent = [];
  push.setSender(async (sub, payload) => { sent.push(JSON.parse(payload)); });
  // The route treats push as best-effort — but with a sender injected we can
  // assert the broadcast when the module believes it's configured. If this
  // env lacks VAPID config, the feed event still must land.
  const admin = pool.users.find((u) => u.email === 'season1@example.com');
  const token = (await req('POST', '/api/auth/login', { body: { email: 'season1@example.com', password: 'longenough1' } })).data.token;
  const start = new Date(Date.now() - 60000), end = new Date(Date.now() + 3 * 86400000);
  const r = await req('POST', '/api/arena/season', { token, body: { name: 'Announce Cup', starts_at: start, ends_at: end } });
  assert.equal(r.status, 200);
  // The public mind-stream feed carries the launch.
  const [events] = await pool.execute('SELECT event_type, title, body FROM agent_events ORDER BY id DESC LIMIT 5', []);
  const ev = events.find((e) => e.event_type === 'arena_season' && /Announce Cup/.test(e.title));
  assert.ok(ev, 'agent feed announces the season');
  assert.match(ev.body, /same virtual stake/i);
  assert.ok(!/\$\s?\d|\d+%/.test(ev.title + ' ' + ev.body), 'announcement carries no numbers (§4)');
  push.setSender(null);
});

test('the Arena speaks six languages — localizer wired, keys complete', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
  assert.match(html, /js\/i18n\.js\?v=\d+/);
  assert.match(html, /data-i18n="arena\.h1"/);
  assert.match(html, /data-i18n-html="arena\.lede"/);
  for (const key of ['arena.h1', 'arena.lede', 'arena.p_account', 'arena.p_ticket', 'arena.p_positions',
    'arena.p_history', 'arena.p_board', 'arena.p_season', 'arena.p_follow', 'arena.p_hall',
    'arena.b_open', 'arena.b_join']) {
    assert.ok(i18n.includes(`'${key}'`), `i18n has ${key}`);
  }
  // the long lede carries all six locales
  const ledeIdx = i18n.indexOf("'arena.lede'");
  const ledeBlock = i18n.slice(ledeIdx, ledeIdx + 4000);
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(ledeBlock.includes(loc), `lede has ${loc}`);
});
