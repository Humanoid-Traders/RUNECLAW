'use strict';
/**
 * Invite friends / referral system — register captures the referrer, each user
 * gets a share code, and /api/auth/referrals reports the code + join count.
 *
 * Runs against the MemoryDB fallback (no DATABASE_URL). Endpoint-driven, mirroring
 * account_management.test.js.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
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

let n = 0;
const reg = (extra = {}) => req('POST', '/api/auth/register',
  { body: { email: `ref${++n}@test.io`, password: 'x'.repeat(12), ...extra } });

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('register mints a referral code and returns it in the session', async () => {
  const r = await reg();
  assert.strictEqual(r.status, 200);
  assert.ok(typeof r.data.referral_code === 'string' && r.data.referral_code.length >= 6,
    'a non-empty referral code is issued');
});

test('registering with ?ref credits the referrer; /referrals counts the join', async () => {
  const a = await reg();
  const code = a.data.referral_code;
  assert.ok(code);

  // Before: A has zero referrals.
  let refs = await req('GET', '/api/auth/referrals', { token: a.data.token });
  assert.strictEqual(refs.status, 200);
  assert.strictEqual(refs.data.code, code);
  assert.strictEqual(refs.data.count, 0);

  // B signs up through A's link.
  const b = await reg({ ref: code });
  assert.strictEqual(b.status, 200);
  assert.notStrictEqual(b.data.referral_code, code); // B gets its own distinct code

  // After: A now has exactly one referral.
  refs = await req('GET', '/api/auth/referrals', { token: a.data.token });
  assert.strictEqual(refs.data.count, 1);
});

test('an unknown or self referral code is ignored, never errors', async () => {
  const bad = await reg({ ref: 'does-not-exist' });
  assert.strictEqual(bad.status, 200); // registration still succeeds
  const self = await reg({ ref: '' });
  assert.strictEqual(self.status, 200);
});

test('/referrals requires auth', async () => {
  const r = await req('GET', '/api/auth/referrals');
  assert.strictEqual(r.status, 401);
});
