/**
 * Multi-venue exchange keys (PR PP): several exchanges connected SIDE BY SIDE.
 *
 * Pins: exchange_status keeps one row per (user, venue) so acking a second
 * venue's connect never clobbers the first; /status exposes venues[] with each
 * exchange's own connected flag; DELETE /api/credentials?venue=X queues a
 * disconnect for ONLY that venue (the other stays connected after the ack);
 * an unknown ?venue is rejected with 400.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.WEB_CREDS_KEY = Buffer.alloc(32, 7).toString('base64');

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');

let server, base, token, uid;

function request(method, path, { token, secret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(secret ? { 'x-bot-secret': secret } : {}),
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

async function ackPending() {
  const ack = await request('POST', '/api/bot/sync/credentials/ack', {
    secret: process.env.BOT_SYNC_SECRET,
    body: { acks: [{ user_id: uid, action: 'connect', ok: true }] },
  });
  assert.strictEqual(ack.status, 200);
}

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['multivenue@test.io', 'x', 'MV']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['multivenue@test.io']);
  uid = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['9002', uid]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
  u[0].telegram_linked = true; // MemoryDB: set the flag directly
  token = jwt.sign({ user_id: uid, email: 'multivenue@test.io' }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/credentials', require('../routes/credentials'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('two venues connect side by side — the second ack never clobbers the first', async () => {
  // Connect Bitget, bot acks.
  let r = await request('POST', '/api/credentials', {
    token, body: { api_key: 'k'.repeat(16), api_secret: 's'.repeat(16), passphrase: 'pp' },
  });
  assert.strictEqual(r.status, 200);
  await ackPending();

  // Connect Bybit on top — a separate row, not a replacement.
  r = await request('POST', '/api/credentials', {
    token, body: { venue: 'bybit', api_key: 'b'.repeat(16), api_secret: 'y'.repeat(16) },
  });
  assert.strictEqual(r.status, 200);
  await ackPending();

  const s = await request('GET', '/api/credentials/status', { token });
  assert.strictEqual(s.status, 200);
  const venues = s.data.venues || [];
  const byId = Object.fromEntries(venues.map(v => [v.venue, v.connected]));
  assert.strictEqual(byId.bitget, true, 'bitget must stay connected after the bybit ack');
  assert.strictEqual(byId.bybit, true, 'bybit must be connected');
  assert.strictEqual(s.data.connected, true); // legacy flag: at least one connected
});

test('DELETE ?venue disconnects only that venue', async () => {
  const r = await request('DELETE', '/api/credentials?venue=bybit', { token });
  assert.strictEqual(r.status, 200);
  // The pending row is venue-stamped so the bot removes only bybit's keys.
  const [pend] = await pool.execute(
    'SELECT action, exchange FROM pending_credentials WHERE user_id = ?', [uid]);
  assert.strictEqual(pend[0].action, 'disconnect');
  assert.strictEqual(pend[0].exchange, 'bybit');

  // Bot acks the disconnect → bybit flips off, bitget untouched.
  const ack = await request('POST', '/api/bot/sync/credentials/ack', {
    secret: process.env.BOT_SYNC_SECRET,
    body: { acks: [{ user_id: uid, action: 'disconnect', ok: true }] },
  });
  assert.strictEqual(ack.status, 200);

  const s = await request('GET', '/api/credentials/status', { token });
  const byId = Object.fromEntries((s.data.venues || []).map(v => [v.venue, v.connected]));
  assert.strictEqual(byId.bybit, false, 'bybit must be disconnected');
  assert.strictEqual(byId.bitget, true, 'bitget must survive the bybit disconnect');
  assert.strictEqual(s.data.connected, true);
  assert.strictEqual(s.data.venue, 'bitget'); // legacy field: first still-connected venue
});

test('DELETE with an unknown venue is rejected', async () => {
  const r = await request('DELETE', '/api/credentials?venue=ftx', { token });
  assert.strictEqual(r.status, 400);
});
