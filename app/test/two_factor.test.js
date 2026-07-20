'use strict';
/**
 * Web 2FA (MH1) — RFC 6238 TOTP + one-time backup codes.
 *
 * The lib is pinned against the RFC's published SHA-1 test vectors, and the
 * flow tests walk the real product path: setup stages (not enables), enable
 * requires a proven code and mints backup codes once, login demands the
 * second factor, a backup code works exactly once, and disable requires a
 * current code so a stolen session can't strip 2FA.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const totp = require('../lib/totp');

// ── RFC vectors ──────────────────────────────────────────────────────────────

test('RFC 6238 SHA-1 test vectors reproduce exactly', () => {
  // Appendix B: ASCII secret "12345678901234567890", 8-digit values — we
  // compare the LAST 6 digits (the lib emits 6, same dynamic truncation).
  const secret = totp.base32Encode(Buffer.from('12345678901234567890'));
  const vectors = [
    [59, '94287082'], [1111111109, '07081804'], [1111111111, '14050471'],
    [1234567890, '89005924'], [2000000000, '69279037'], [20000000000, '65353130'],
  ];
  for (const [t, expected] of vectors) {
    const counter = Math.floor(t / 30);
    assert.equal(totp.hotp(secret, counter), expected.slice(-6), `T=${t}`);
  }
});

test('verifyTotp accepts ±1 step of drift, rejects junk and replay-window misses', () => {
  const secret = totp.generateSecret();
  const now = 1_700_000_000_000;
  const counter = Math.floor(now / 30_000);
  assert.ok(totp.verifyTotp(secret, totp.hotp(secret, counter), now));
  assert.ok(totp.verifyTotp(secret, totp.hotp(secret, counter - 1), now));
  assert.ok(totp.verifyTotp(secret, totp.hotp(secret, counter + 1), now));
  assert.ok(!totp.verifyTotp(secret, totp.hotp(secret, counter + 5), now));
  assert.ok(!totp.verifyTotp(secret, 'abcdef', now));
  assert.ok(!totp.verifyTotp(secret, '', now));
});

test('backup codes: single-use, hash-stored, format-tolerant', () => {
  const { codes, hashes } = totp.generateBackupCodes();
  assert.equal(codes.length, 8);
  const spaced = codes[0].toLowerCase().replace('-', ' ');
  const remaining = totp.consumeBackupCode(spaced, hashes);
  assert.equal(remaining.length, 7);
  assert.equal(totp.consumeBackupCode(codes[0], remaining), null, 'used once only');
});

// ── Flow ─────────────────────────────────────────────────────────────────────

let server, base;

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
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
  app.use('/api/auth', require('../auth').router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('full 2FA lifecycle: setup → enable → gated login → backup → disable', async () => {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12) } });
  assert.equal(reg.status, 200);
  const token = reg.data.token;

  // Fresh account: 2FA off, plain login works.
  let st = await req('GET', '/api/auth/2fa/status', { token });
  assert.deepEqual(st.data, { enabled: false, pending: false, backup_codes_remaining: null });

  // Setup stages a secret but does NOT enable — a wrong code can't lock out.
  const setup = await req('POST', '/api/auth/2fa/setup', { token });
  assert.equal(setup.status, 200);
  assert.match(setup.data.otpauth, /^otpauth:\/\/totp\/RUNECLAW/);
  const login0 = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12) } });
  assert.equal(login0.status, 200, 'pending setup never gates login');

  // Enable requires a valid code; wrong code refused.
  const bad = await req('POST', '/api/auth/2fa/enable', { token, body: { code: '000000' } });
  assert.equal(bad.status, 401);
  const good = await req('POST', '/api/auth/2fa/enable',
    { token, body: { code: totp.hotp(setup.data.secret, Math.floor(Date.now() / 30_000)) } });
  assert.equal(good.status, 200);
  assert.equal(good.data.backup_codes.length, 8);

  // Login now demands the second factor.
  const noCode = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12) } });
  assert.equal(noCode.status, 401);
  assert.equal(noCode.data.two_factor_required, true);
  const wrong = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12), totp_code: '000000' } });
  assert.equal(wrong.status, 401);
  const withCode = await req('POST', '/api/auth/login', {
    body: { email: 'tfa1@test.io', password: 'x'.repeat(12),
      totp_code: totp.hotp(setup.data.secret, Math.floor(Date.now() / 30_000)) },
  });
  assert.equal(withCode.status, 200);
  assert.ok(withCode.data.token);

  // A backup code logs in exactly once.
  const backup = good.data.backup_codes[0];
  const b1 = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12), totp_code: backup } });
  assert.equal(b1.status, 200);
  const b2 = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12), totp_code: backup } });
  assert.equal(b2.status, 401, 'backup code is single-use');
  st = await req('GET', '/api/auth/2fa/status', { token });
  assert.equal(st.data.backup_codes_remaining, 7);

  // Disable requires a current (or backup) code — a bare session is not enough.
  const noDis = await req('POST', '/api/auth/2fa/disable', { token, body: { code: '000000' } });
  assert.equal(noDis.status, 401);
  const dis = await req('POST', '/api/auth/2fa/disable', {
    token, body: { code: totp.hotp(setup.data.secret, Math.floor(Date.now() / 30_000)) },
  });
  assert.equal(dis.status, 200);
  const loginAfter = await req('POST', '/api/auth/login',
    { body: { email: 'tfa1@test.io', password: 'x'.repeat(12) } });
  assert.equal(loginAfter.status, 200, 'clean single-factor login after disable');
});

test('landing page carries the 2FA wiring', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(html, /login-2fa-box/, 'login second-step field');
  assert.match(html, /two_factor_required/, 'login handles the 2FA gate');
  assert.match(html, /2fa\/setup/, 'account panel setup flow');
  assert.match(html, /Backup codes — save them now/, 'backup codes shown once');
});
