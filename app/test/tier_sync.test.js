'use strict';
/**
 * Membership tier sync — the bot is the tier authority; the web mirrors it.
 * users.plan may ONLY change through the X-Bot-Secret-authed sync endpoint
 * (i.e. a /set_tier in Telegram), never from the browser.
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

function req(method, path, { token, botSecret, body } = {}) {
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

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('tier sync updates plan by telegram_id, rejects junk, requires bot secret', async () => {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'tier@test.io', password: 'x'.repeat(12) } });
  assert.ok(reg.data.token);
  const uid = reg.data.user_id;

  // Simulate a completed Telegram link so the user has a telegram_id.
  await pool.execute(
    'UPDATE users SET telegram_id = ? WHERE id = ?', ['777001', uid]);

  // No bot secret -> rejected before any write.
  let r = await req('POST', '/api/bot/sync/tiers',
    { body: { tiers: [{ telegram_id: '777001', tier: 'pro' }] } });
  assert.strictEqual(r.status, 403);   // botAuth rejects before any write

  // Authed sync: valid row applies, junk tier and unknown user are skipped.
  r = await req('POST', '/api/bot/sync/tiers', {
    botSecret: process.env.BOT_SYNC_SECRET,
    body: { tiers: [
      { telegram_id: '777001', tier: 'pro' },
      { telegram_id: '777001', tier: 'DROP TABLE users' },   // invalid tier
      { telegram_id: '999999', tier: 'elite' },              // unknown user
    ] },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.updated, 1);

  // The user's /me now reflects the bot-granted plan.
  const me = await req('GET', '/api/auth/me', { token: reg.data.token });
  assert.strictEqual(me.data.plan, 'pro');
});
