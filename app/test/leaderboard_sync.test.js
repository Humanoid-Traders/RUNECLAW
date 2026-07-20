'use strict';
/**
 * Bot sync — leaderboard opt-in desired-state pull (community C2).
 * GET /api/bot/sync/leaderboard/pending returns every user who opted in
 * (anonymous handle set) AND linked their bot account; bot-secret authed;
 * handle-only users without a telegram link are excluded (nothing to publish).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');

let server, base;

function req(method, path, { token, body, botSecret } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let n = 0;
async function newUser() {
  n++;
  const r = await req('POST', '/api/auth/register',
    { body: { email: `lbsync${n}@test.io`, password: 'x'.repeat(12) } });
  assert.equal(r.status, 200);
  return { token: r.data.token, id: r.data.user_id };
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('pending returns opted-in AND bot-linked users only, aliased fields', async () => {
  const linked = await newUser();
  const unlinked = await newUser();
  // Both opt in; only one links a bot account.
  let r = await req('POST', '/api/leaderboard/opt-in',
    { token: linked.token, body: { handle: 'runefox' } });
  assert.equal(r.status, 200);
  r = await req('POST', '/api/leaderboard/opt-in',
    { token: unlinked.token, body: { handle: 'ghost_user' } });
  assert.equal(r.status, 200);
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['111', linked.id]);

  r = await req('GET', '/api/bot/sync/leaderboard/pending',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.equal(r.status, 200);
  const rows = r.data.optins;
  assert.ok(Array.isArray(rows));
  const mine = rows.find(x => x.handle === 'runefox');
  assert.ok(mine, 'linked opted-in user is present');
  assert.equal(String(mine.telegram_id), '111');
  assert.equal(mine.user_id, linked.id);
  assert.ok(!rows.some(x => x.handle === 'ghost_user'),
    'handle without a bot link is excluded — nothing to publish');
});

test('opt-out drops the user from the desired state', async () => {
  const u = await newUser();
  await req('POST', '/api/leaderboard/opt-in', { token: u.token, body: { handle: 'brieffox' } });
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['222', u.id]);
  let r = await req('GET', '/api/bot/sync/leaderboard/pending',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.ok(r.data.optins.some(x => x.handle === 'brieffox'));

  r = await req('POST', '/api/leaderboard/opt-out', { token: u.token });
  assert.equal(r.status, 200);
  r = await req('GET', '/api/bot/sync/leaderboard/pending',
    { botSecret: process.env.BOT_SYNC_SECRET });
  assert.ok(!r.data.optins.some(x => x.handle === 'brieffox'),
    'opt-out removes the row from the desired state');
});

test('bot secret required: wrong or missing secret is rejected', async () => {
  let r = await req('GET', '/api/bot/sync/leaderboard/pending');
  assert.equal(r.status, 403);
  r = await req('GET', '/api/bot/sync/leaderboard/pending', { botSecret: 'wrong' });
  assert.equal(r.status, 403);
});
