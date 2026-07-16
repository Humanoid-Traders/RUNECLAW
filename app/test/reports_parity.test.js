'use strict';
/**
 * Web↔Telegram parity: bot-pushed reports cache + admin-gated yield + the
 * admin-only stance queue. Yield contains operator balances so /api/reports
 * exposes only a presence flag; the content needs a logged-in admin. Stance
 * is the bot's GLOBAL posture: non-admins get 403 before any write.
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

const SECRET = process.env.BOT_SYNC_SECRET;
const SAMPLE = {
  generated_at: '2026-07-16T12:00:00Z',
  funding: { rows: [{ base: 'BTC', rates: { bitget: 8.2, bybit: 10.9 }, spread_apr: 2.7, long_venue: 'bitget', short_venue: 'bybit' }] },
  arb: { notional_usd: 1000, snapshots: 12, carries: [{ base: 'ALPHA', earned_usd: 1.5, held_hours: 10, observed_hours: 12, entries: 2, last_spread_apr: 11.2, venues: 'bitget/bybit' }] },
  parity: { trades: 18, win_rate: 0.61, net_pnl: 4.51, pf: 2.24, fee_vs_model: 0.48, inferred_fills: 14, excluded_non_fills: 7 },
  yield: { total_idle_usd: 42.5, total_est_year_usd: 3.1, rows: [{ coin: 'USDT', idle_usd: 42.5, stakeable_usd: 30.0, apy_flexible: 7.3, est_year_usd: 3.1 }] },
};

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/reports', require('../routes/reports'));
  app.use('/api/controls', require('../routes/controls'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('reports: bot push requires secret; public read hides yield content', async () => {
  let r = await req('POST', '/api/bot/sync/reports', { body: SAMPLE });
  assert.strictEqual(r.status, 403);

  r = await req('POST', '/api/bot/sync/reports', { botSecret: SECRET, body: SAMPLE });
  assert.strictEqual(r.status, 200);

  const read = await req('GET', '/api/reports');
  assert.strictEqual(read.status, 200);
  const rep = read.data.reports;
  assert.strictEqual(rep.funding.rows[0].base, 'BTC');
  assert.strictEqual(rep.arb.carries[0].base, 'ALPHA');
  assert.strictEqual(rep.parity.pf, 2.24);
  assert.strictEqual(rep.has_yield, true);
  assert.strictEqual(rep.yield, undefined);   // content never on the public route
});

test('yield report: 401 anonymous, 403 basic plan, 200 admin', async () => {
  let r = await req('GET', '/api/reports/yield');
  assert.strictEqual(r.status, 401);

  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'basicy@test.io', password: 'x'.repeat(12) } });
  r = await req('GET', '/api/reports/yield', { token: reg.data.token });
  assert.strictEqual(r.status, 403);

  const admin = await req('POST', '/api/auth/register',
    { body: { email: 'adminy@test.io', password: 'x'.repeat(12) } });
  await pool.execute('UPDATE users SET TELEGRAM_ID = ? WHERE id = ?', ['555001', admin.data.user_id]);
  await req('POST', '/api/bot/sync/tiers', {
    botSecret: SECRET, body: { tiers: [{ telegram_id: '555001', tier: 'admin' }] } });
  r = await req('GET', '/api/reports/yield', { token: admin.data.token });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.yield.rows[0].coin, 'USDT');
});

test('stance queue: admin-only, telegram-linked, bot round trip clears row', async () => {
  const user = await req('POST', '/api/auth/register',
    { body: { email: 'stance@test.io', password: 'x'.repeat(12) } });

  // Non-admin: refused before any write.
  let r = await req('POST', '/api/controls/stance',
    { token: user.data.token, body: { mode: 'defensive' } });
  assert.strictEqual(r.status, 403);

  // Promote to admin via the bot tier authority + link telegram.
  await pool.execute('UPDATE users SET TELEGRAM_ID = ? WHERE id = ?', ['555002', user.data.user_id]);
  await req('POST', '/api/bot/sync/tiers', {
    botSecret: SECRET, body: { tiers: [{ telegram_id: '555002', tier: 'admin' }] } });

  // Invalid mode still rejected.
  r = await req('POST', '/api/controls/stance',
    { token: user.data.token, body: { mode: 'yolo' } });
  assert.strictEqual(r.status, 400);

  // Valid queue. (MemoryDB users always report telegram_linked via the
  // telegram_id we set; if the route 409s the fixture needs linking too.)
  r = await req('POST', '/api/controls/stance',
    { token: user.data.token, body: { mode: 'defensive' } });
  if (r.status === 409) {
    await pool.execute('UPDATE users SET TELEGRAM_LINKED = ? WHERE id = ?', [1, user.data.user_id]);
    r = await req('POST', '/api/controls/stance',
      { token: user.data.token, body: { mode: 'defensive' } });
  }
  assert.strictEqual(r.status, 200);

  // Bot round trip: pending -> ack clears.
  r = await req('GET', '/api/bot/sync/stance/pending', { botSecret: SECRET });
  assert.strictEqual(r.data.pending.mode, 'defensive');
  assert.strictEqual(String(r.data.pending.telegram_id), '555002');
  r = await req('POST', '/api/bot/sync/stance/ack', { botSecret: SECRET, body: { applied: true } });
  assert.strictEqual(r.status, 200);
  r = await req('GET', '/api/bot/sync/stance/pending', { botSecret: SECRET });
  assert.strictEqual(r.data.pending, null);
});
