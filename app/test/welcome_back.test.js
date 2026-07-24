'use strict';
/**
 * "While you were away" — /api/since gives the dashboard's welcome-back
 * digest: real counts of what happened during the caller's absence (new
 * signals, engine events, own paper closes + vUSDT pnl — a PRIVATE per-user
 * surface, so virtual dollars are allowed). Honesty doctrine: a first visit
 * returns { first_visit: true } and never a back-filled history; reading the
 * digest advances the window so nothing is ever double-reported.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/since', require('../routes/since'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

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
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const EMAIL = 'away@example.com';
let token, userId;

test('the digest requires auth', async () => {
  const r = await req('GET', '/api/since');
  assert.equal(r.status, 401);
});

test('first visit is honest: no back-filled history, window starts now', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: EMAIL, password: 'longenough1' } });
  assert.equal(reg.status, 200);
  token = reg.data.token;
  userId = pool.users.find((u) => u.email === EMAIL).id;
  const r = await req('GET', '/api/since', { token });
  assert.equal(r.status, 200);
  assert.deepEqual(r.data, { first_visit: true });
  assert.ok(pool.users.find((u) => u.id === userId).last_seen_at, 'first read stamps last_seen_at');
});

test('a quick return shows a tiny window with zero activity', async () => {
  const r = await req('GET', '/api/since', { token });
  assert.equal(r.status, 200);
  assert.ok(r.data.away_s < 60);
  assert.equal(r.data.signals_new, 0);
  assert.equal(r.data.events_new, 0);
  assert.deepEqual(r.data.arena, { closes: 0, pnl: 0 });
});

test('after a real absence the digest counts what actually happened', async () => {
  // Simulate 10 hours away…
  pool.users.find((u) => u.id === userId).last_seen_at = new Date(Date.now() - 10 * 3600 * 1000);
  const now = new Date();
  // …during which: one signal, two engine events, one own close (+50), and
  // a close by SOMEBODY ELSE (must not leak into the caller's arena digest).
  pool.signals.push({ id: 9001, symbol: 'BTCUSDT', direction: 'LONG', created_at: now });
  pool.agentEvents.push(
    { id: 9001, event_type: 'scan', severity: 'info', title: 't', created_at: now },
    { id: 9002, event_type: 'scan', severity: 'info', title: 't', created_at: now });
  pool.arenaTrades.push(
    { id: 9001, user_id: userId, symbol: 'ETHUSDT', direction: 'LONG', entry: 100,
      exit_price: 105, margin: 500, leverage: 2, pnl: 50, reason: 'manual',
      opened_at: now, closed_at: now },
    { id: 9002, user_id: userId + 999, symbol: 'ETHUSDT', direction: 'SHORT', entry: 100,
      exit_price: 90, margin: 500, leverage: 2, pnl: 100, reason: 'tp',
      opened_at: now, closed_at: now });
  const r = await req('GET', '/api/since', { token });
  assert.equal(r.status, 200);
  assert.ok(r.data.away_s >= 10 * 3600 - 5);
  assert.equal(r.data.signals_new, 1);
  assert.equal(r.data.events_new, 2);
  assert.equal(r.data.arena.closes, 1, 'only the caller\'s own closes');
  assert.equal(r.data.arena.pnl, 50);
});

test('reading the digest advances the window — nothing double-reports', async () => {
  const r = await req('GET', '/api/since', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.signals_new, 0);
  assert.equal(r.data.events_new, 0);
  assert.equal(r.data.arena.closes, 0);
});

// ---- Shipped page wiring (source assertions) ----------------------------

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the dashboard fetches the digest once, gates on a real absence, and can dismiss', () => {
  assert.match(dash, /\/api\/since/);
  assert.match(dash, /first_visit/);
  assert.match(dash, /6 \* 3600/);                 // 6h+ absences only
  assert.match(dash, /id="p-since"/);
  assert.match(dash, /sinceDismiss/);
  assert.match(dash, /While you were away/);
  const m = html.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 96, `dashboard.js version floor (got ${m && m[1]})`);
});
