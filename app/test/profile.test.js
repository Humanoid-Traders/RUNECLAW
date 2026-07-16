'use strict';
/**
 * Per-user agent profile: JWT-authed, validated/whitelisted writes, merge
 * semantics, and per-user isolation. risk_pref is the user's OWN preference
 * — it must never touch the operator stance queue.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');

let server, base;

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
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
  app.use('/api/profile', require('../routes/profile'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

async function register(email) {
  const r = await req('POST', '/api/auth/register', { body: { email, password: 'x'.repeat(12) } });
  assert.ok(r.data.token, `register ${email}`);
  return r.data.token;
}

test('profile requires auth; defaults; validated merge writes', async () => {
  let r = await req('GET', '/api/profile');
  assert.strictEqual(r.status, 401);

  const token = await register('prof1@test.io');
  r = await req('GET', '/api/profile', { token });
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.data, { risk_pref: null, watchlist: [], prefs: {} });

  // Invalid risk_pref rejected before any write.
  r = await req('PUT', '/api/profile', { token, body: { risk_pref: 'yolo' } });
  assert.strictEqual(r.status, 400);

  // Valid write: symbols are normalized/deduped, junk prefs keys dropped,
  // numeric prefs clamped.
  r = await req('PUT', '/api/profile', { token, body: {
    risk_pref: 'Conservative',
    watchlist: ['btcusdt', 'SOL-USDT', 'SOLUSDT', 'x'],   // x too short -> dropped
    prefs: { default_leverage: 9999, chart_tf: '4h', evil_key: 'nope',
             default_margin_usd: 25.5, chart_symbol: 'eth/usdt!' },
  } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.risk_pref, 'conservative');
  assert.deepStrictEqual(r.data.watchlist, ['BTCUSDT', 'SOLUSDT']);
  assert.strictEqual(r.data.prefs.default_leverage, 125);      // clamped
  assert.strictEqual(r.data.prefs.default_margin_usd, 25.5);
  assert.strictEqual(r.data.prefs.chart_tf, '4h');
  assert.strictEqual(r.data.prefs.chart_symbol, 'ETHUSDT');
  assert.strictEqual(r.data.prefs.evil_key, undefined);

  // Merge semantics: patching one field keeps the others.
  r = await req('PUT', '/api/profile', { token, body: { watchlist: ['LINKUSDT'] } });
  assert.strictEqual(r.data.risk_pref, 'conservative');
  assert.deepStrictEqual(r.data.watchlist, ['LINKUSDT']);
  assert.strictEqual(r.data.prefs.chart_tf, '4h');

  // Oversized watchlist rejected.
  const big = Array.from({ length: 21 }, (_, i) => `AA${i}USDT`);
  r = await req('PUT', '/api/profile', { token, body: { watchlist: big } });
  assert.strictEqual(r.status, 400);

  // risk_pref null clears it.
  r = await req('PUT', '/api/profile', { token, body: { risk_pref: null } });
  assert.strictEqual(r.data.risk_pref, null);
});

test('profiles are isolated per user', async () => {
  const a = await register('prof2a@test.io');
  const b = await register('prof2b@test.io');
  await req('PUT', '/api/profile', { token: a, body: { risk_pref: 'aggressive' } });
  const rb = await req('GET', '/api/profile', { token: b });
  assert.strictEqual(rb.data.risk_pref, null);
  const ra = await req('GET', '/api/profile', { token: a });
  assert.strictEqual(ra.data.risk_pref, 'aggressive');
});
