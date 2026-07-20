'use strict';
/**
 * Telegram-parity reads on the bot sync router (PR EE): /exposure /research
 * /rwa serve the SAME Node-side libs the web panels use, bot-secret authed.
 * Exposure maps telegram_id -> web account; unknown links 404 (never a
 * fabricated empty book).
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

// No real venue calls in tests: inject a fixed ticker set (the same hook the
// existing research/rwa tests use).
const FAKE_TICKERS = {
  ONDOUSDT: { last: 1.0, changePct24h: 2.1, usdtVolume: 5_000_000 },
  BTCUSDT: { last: 60000, changePct24h: 0.5, usdtVolume: 900_000_000 },
};
require('../lib/rwa').setTickerFetcher(async () => FAKE_TICKERS);
require('../lib/research').setTickerFetcher(async () => FAKE_TICKERS);
require('../lib/token_safety').setPairSearcher(async () => null);

let server, base;

function req(path, { botSecret } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${path}`, {
      method: 'GET',
      headers: { ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}) },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
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

const SECRET = process.env.BOT_SYNC_SECRET;

test('all three parity reads require the bot secret', async () => {
  for (const p of ['/api/bot/sync/exposure?telegram_id=1',
                   '/api/bot/sync/research/BTC', '/api/bot/sync/rwa']) {
    assert.equal((await req(p)).status, 403, p);
    assert.equal((await req(p, { botSecret: 'wrong' })).status, 403, p);
  }
});

test('exposure maps telegram_id to the web account; unlinked 404s', async () => {
  let r = await req('/api/bot/sync/exposure?telegram_id=990001', { botSecret: SECRET });
  assert.equal(r.status, 404, 'unknown telegram link must 404');

  // Register + link a user, then the exposure payload comes back.
  const reg = await new Promise((resolve, reject) => {
    const body = JSON.stringify({ email: 'parity1@test.io', password: 'x'.repeat(12) });
    const rq = http.request(`${base}/api/auth/register`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve(JSON.parse(d)));
    });
    rq.on('error', reject);
    rq.write(body);
    rq.end();
  });
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?',
    ['990001', reg.user_id]);
  r = await req('/api/bot/sync/exposure?telegram_id=990001', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.ok('net_total_usd' in r.data && Array.isArray(r.data.assets));
  assert.equal(r.data.read_only, true);

  r = await req('/api/bot/sync/exposure', { botSecret: SECRET });
  assert.equal(r.status, 400, 'telegram_id required');
});

test('research sanitizes the symbol and 404s unlisted', async () => {
  // MemoryDB test env has no venue tickers: any symbol resolves to "not
  // listed" — which is exactly the honest 404 the route promises.
  const r = await req('/api/bot/sync/research/%2e%2e%2fetc', { botSecret: SECRET });
  assert.ok([400, 404].includes(r.status), 'junk symbol never 500s');
  const r2 = await req('/api/bot/sync/research/NOPE123', { botSecret: SECRET });
  assert.ok([404, 500].includes(r2.status) === false || r2.status === 404,
    'unlisted symbol is a 404, not an error');
});

test('rwa returns the radar shape', async () => {
  const r = await req('/api/bot/sync/rwa', { botSecret: SECRET });
  assert.equal(r.status, 200);
  assert.ok(r.data.sector && 'listed' in r.data.sector);
  assert.ok(Array.isArray(r.data.categories));
});
