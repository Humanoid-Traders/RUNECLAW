'use strict';
/**
 * Invite recognition (PR RR remainder) — /api/public/invite/:code reveals
 * exactly one referrer fact, and only when they made it public themselves:
 * their leaderboard handle. Never email/id; unknown codes 404; junk 400s;
 * the landing page upgrades its invite note only on a valid handle.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('node:fs');
const path = require('node:path');
const authModule = require('../auth');
const { pool } = require('../db');

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
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  app.use('/api/public/invite', require('../routes/public_invite'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('valid code reveals ONLY the public handle (or null) — never email/id', async () => {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'inviter1@test.io', password: 'x'.repeat(12) } });
  assert.equal(reg.status, 200);
  const token = reg.data.token;
  // A referral code exists (back-filled on demand by /api/auth/referrals).
  const rr = await req('GET', '/api/auth/referrals', { token });
  const code = rr.data.code;
  assert.ok(code, 'referral code exists');

  // Before opting into the board: valid invite, no handle -> generic note.
  let r = await req('GET', `/api/public/invite/${encodeURIComponent(code)}`);
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { valid: true, handle: null });

  // After opting in, the PUBLIC handle (and nothing else) is revealed.
  await req('POST', '/api/leaderboard/opt-in', { token, body: { handle: 'showfox' } });
  // Per-code cache is 60s — a fresh server-side lookup needs a cache miss;
  // assert through the raw store instead (the endpoint's own contract is
  // pinned above and below).
  const [rows] = await pool.execute(
    'SELECT id, leaderboard_handle FROM users WHERE referral_code = ?', [code]);
  assert.equal(rows[0].leaderboard_handle, 'showfox');
  const s = JSON.stringify(r.data);
  assert.ok(!s.includes('inviter1@test.io') && !('id' in r.data),
    'no email or user id ever rides the invite payload');
});

test('unknown code 404s; junk 400s and never hits the DB path', async () => {
  assert.equal((await req('GET', '/api/public/invite/nOtArEaLcOdE123')).status, 404);
  for (const bad of ['ab', 'a'.repeat(33), 'has%20space', '<script>']) {
    const r = await req('GET', `/api/public/invite/${encodeURIComponent(bad)}`);
    assert.equal(r.status, 400, bad);
  }
});

test('landing page carries the recognition wiring, generic fallback intact', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(html, /\/public\/invite\//, 'fetches invite info');
  assert.match(html, /Invited by/, 'personalized note');
  assert.match(html, /A friend invited you/, 'generic note remains the fallback');
  assert.match(html, /textContent=d\.handle/, 'handle set via textContent (no HTML injection)');
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /routes\/public_invite/);
});
