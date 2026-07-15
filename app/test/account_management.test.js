'use strict';
/**
 * Account management: email verification + password reset + change password,
 * and the dependency-free SMTP mailer.
 *
 * The mailer is exercised as a pure unit (message assembly, config gating,
 * no-op when unconfigured). The auth routes run against the MemoryDB fallback
 * with mailer.sendMail monkeypatched to capture the token-bearing link that
 * would have been emailed — so we can drive verify/reset end-to-end.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL; // force MemoryDB
// Ensure the mailer is "not configured" for the no-op assertions.
delete process.env.SMTP_HOST;
delete process.env.MAIL_FROM;

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');

const mailer = require('../lib/mailer');
// Keep a handle to the real sender — the integration section below monkeypatches
// mailer.sendMail, and that patch runs at load time (before any test executes).
const realSendMail = mailer.sendMail;

// --- Mailer unit tests (pure) ---

test('mailer is a no-op when SMTP is unconfigured', async () => {
  assert.strictEqual(mailer.isConfigured(), false);
  const r = await realSendMail({ to: 'a@b.com', subject: 'Hi', text: 'body' });
  assert.strictEqual(r.skipped, true);
  assert.strictEqual(r.reason, 'not_configured');
});

test('buildMessage produces RFC5322 headers + base64 body, dot-stuffed', () => {
  const msg = mailer.buildMessage({
    from: 'RUNECLAW <no-reply@runeclaw.app>',
    to: 'user@example.com',
    subject: 'Verify your RUNECLAW email',
    text: '.leading dot line\nsecond line',
  });
  assert.match(msg, /^From: RUNECLAW <no-reply@runeclaw\.app>\r\n/);
  assert.match(msg, /\r\nTo: user@example\.com\r\n/);
  assert.match(msg, /\r\nSubject: Verify your RUNECLAW email\r\n/);
  assert.match(msg, /Content-Transfer-Encoding: base64/);
  // A line that began with '.' in the body must be dot-stuffed to '..'.
  assert.ok(msg.includes('\r\n..') || !msg.includes('\r\n.leading'));
});

test('_addrOnly extracts the bare address from a display-name form', () => {
  assert.strictEqual(mailer._addrOnly('RUNECLAW <no-reply@x.io>'), 'no-reply@x.io');
  assert.strictEqual(mailer._addrOnly('plain@x.io'), 'plain@x.io');
});

test('_encodeHeader RFC2047-encodes non-ASCII subjects only', () => {
  assert.strictEqual(mailer._encodeHeader('Plain ASCII'), 'Plain ASCII');
  assert.match(mailer._encodeHeader('Café ☕'), /^=\?UTF-8\?B\?.+\?=$/);
});

// --- Auth route integration (MemoryDB + captured emails) ---

// Capture emails instead of sending. auth.js reads mailer.sendMail at call
// time, so overriding the property here intercepts its calls.
const sentEmails = [];
mailer.sendMail = async ({ to, subject, text, html }) => {
  sentEmails.push({ to, subject, text, html });
  return { skipped: false };
};
// Make baseUrl deterministic for link assertions.
process.env.APP_BASE_URL = 'https://app.test';

const { router: authRouter } = require('../auth');

let server;
let base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authRouter);
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
});

test.after(() => { if (server) server.close(); });

function post(path, body, token) {
  const headers = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(base + path, { method: 'POST', headers, body: JSON.stringify(body) })
    .then(async (r) => ({ status: r.status, json: await r.json() }));
}

function tokenFromLastEmail(re) {
  const last = sentEmails[sentEmails.length - 1];
  const m = re.exec(last.text);
  return m ? m[1] : null;
}

test('register sends a verification email with a token link', async () => {
  sentEmails.length = 0;
  const r = await post('/api/auth/register', { email: 'ver@example.com', password: 'longenough1' });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.json.email_verified, false);
  assert.strictEqual(sentEmails.length, 1);
  assert.match(sentEmails[0].text, /https:\/\/app\.test\/verify\?token=[a-f0-9]{64}/);
});

test('verify-email marks the account verified; a bad token is rejected', async () => {
  await post('/api/auth/register', { email: 'v2@example.com', password: 'longenough1' });
  const raw = tokenFromLastEmail(/verify\?token=([a-f0-9]+)/);
  assert.ok(raw);
  const bad = await post('/api/auth/verify-email', { token: 'deadbeef' });
  assert.strictEqual(bad.status, 400);
  const ok = await post('/api/auth/verify-email', { token: raw });
  assert.strictEqual(ok.status, 200);
  assert.strictEqual(ok.json.ok, true);
});

test('forgot-password returns a generic body and issues a reset token', async () => {
  await post('/api/auth/register', { email: 'reset@example.com', password: 'longenough1' });
  sentEmails.length = 0;
  const r = await post('/api/auth/forgot-password', { email: 'reset@example.com' });
  assert.strictEqual(r.status, 200);
  assert.ok(r.json.message); // generic
  assert.strictEqual(sentEmails.length, 1);
  assert.match(sentEmails[0].text, /\/reset\?token=[a-f0-9]{64}/);
});

test('forgot-password for an unknown email is a silent no-send (no enumeration)', async () => {
  sentEmails.length = 0;
  const r = await post('/api/auth/forgot-password', { email: 'nobody@example.com' });
  assert.strictEqual(r.status, 200);
  assert.ok(r.json.message);
  assert.strictEqual(sentEmails.length, 0, 'must not send for a non-existent account');
});

test('reset-password with the token sets a new password; login works with it', async () => {
  await post('/api/auth/register', { email: 'flow@example.com', password: 'oldpassword1' });
  await post('/api/auth/forgot-password', { email: 'flow@example.com' });
  const raw = tokenFromLastEmail(/reset\?token=([a-f0-9]+)/);
  assert.ok(raw);
  const bad = await post('/api/auth/reset-password', { token: raw, new_password: 'short' });
  assert.strictEqual(bad.status, 400, 'weak password rejected');
  const done = await post('/api/auth/reset-password', { token: raw, new_password: 'brandnewpass1' });
  assert.strictEqual(done.status, 200);
  const login = await post('/api/auth/login', { email: 'flow@example.com', password: 'brandnewpass1' });
  assert.strictEqual(login.status, 200);
  assert.ok(login.json.token);
  // The used token can't be replayed.
  const replay = await post('/api/auth/reset-password', { token: raw, new_password: 'anotherpass12' });
  assert.strictEqual(replay.status, 400);
});

test('change-password requires the correct current password', async () => {
  const reg = await post('/api/auth/register', { email: 'chg@example.com', password: 'currentpass1' });
  const token = reg.json.token;
  const wrong = await post('/api/auth/change-password', { current_password: 'nope', new_password: 'newerpass123' }, token);
  assert.strictEqual(wrong.status, 401);
  const ok = await post('/api/auth/change-password', { current_password: 'currentpass1', new_password: 'newerpass123' }, token);
  assert.strictEqual(ok.status, 200);
  const login = await post('/api/auth/login', { email: 'chg@example.com', password: 'newerpass123' });
  assert.strictEqual(login.status, 200);
});

test('change-password requires auth', async () => {
  const r = await post('/api/auth/change-password', { new_password: 'whatever12345' });
  assert.strictEqual(r.status, 401);
});
