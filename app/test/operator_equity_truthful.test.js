/**
 * Operator equity truthfulness (routes/sync.js write + routes/portfolio.js read).
 *
 * The operator trades LIVE and has no paper baseline. Pins:
 *  - a real synced equity shows through under a LIVE header;
 *  - an "unavailable" reading (equity null) is NOT coerced to 0/$10k and does
 *    not clobber the last real snapshot;
 *  - when the bot's circuit_breaker signals live_unavailable, the read path
 *    returns equity:null + live_unavailable:true (LIVE, but no fake number).
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
process.env.BOT_USER_ID = '1';

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const jwt = require('jsonwebtoken');
const { pool } = require('../db');

let server, base, opToken;
const SECRET = process.env.BOT_SYNC_SECRET;

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

async function setLiveMode(fields) {
  await pool.execute('REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
    [JSON.stringify({ circuit_breaker: { live_mode: true, ...fields } })]);
}

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['op@test.io', 'x', 'Operator']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['op@test.io']);
  assert.strictEqual(rows[0].id, 1, 'operator must be user id 1 (=BOT_USER_ID)');
  opToken = jwt.sign({ user_id: 1, email: 'op@test.io' }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/portfolio', require('../routes/portfolio'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('a real synced equity shows through under a LIVE header', async () => {
  await setLiveMode({});
  const s = await request('POST', '/api/bot/sync', { secret: SECRET, body: { equity: 8200.5, positions: [], closed_trades: [] } });
  assert.strictEqual(s.status, 200);
  const p = await request('GET', '/api/portfolio', { token: opToken });
  assert.strictEqual(p.data.mode, 'LIVE');
  assert.strictEqual(p.data.equity, 8200.5);
  assert.strictEqual(p.data.live_unavailable, false);
});

test('an unavailable (null) equity is not coerced to 0 and does not clobber the last real snapshot', async () => {
  // Bot reports live balance unavailable this cycle → equity null in the body.
  const s = await request('POST', '/api/bot/sync', { secret: SECRET, body: { equity: null, positions: [], closed_trades: [] } });
  assert.strictEqual(s.status, 200);
  assert.strictEqual(s.data.synced.equity, null, 'never a fabricated 0');
  const p = await request('GET', '/api/portfolio', { token: opToken });
  // Last real snapshot (8200.5, still fresh) is preserved — NOT overwritten by 0.
  assert.strictEqual(p.data.equity, 8200.5);
  assert.notStrictEqual(p.data.equity, 0);
});

test('circuit_breaker.live_unavailable → equity null + live_unavailable, never paper', async () => {
  await setLiveMode({ live_unavailable: true });
  const p = await request('GET', '/api/portfolio', { token: opToken });
  assert.strictEqual(p.data.mode, 'LIVE');
  assert.strictEqual(p.data.equity, null, 'LIVE but unreadable → null, never $10k/paper');
  assert.strictEqual(p.data.live_unavailable, true);
});

test('fresh scan-cycle equity shows through even with no recent close-driven snapshot', async () => {
  // The bot refreshes circuit_breaker.equity every scan cycle; snapshots only
  // update on live CLOSES. A quiet stretch must show the scan reading, not
  // "BALANCE UNAVAILABLE" (the live-site regression of 2026-07-16).
  await pool.execute('REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
    [JSON.stringify({
      received_at: new Date().toISOString(),
      circuit_breaker: { live_mode: true, live_unavailable: false, equity: 128.99 },
    })]);
  const p = await request('GET', '/api/portfolio', { token: opToken });
  assert.strictEqual(p.data.mode, 'LIVE');
  assert.strictEqual(p.data.equity, 128.99);
  assert.strictEqual(p.data.live_unavailable, false);
});

test('a STALE scan-cycle equity does not mask unavailability', async () => {
  await pool.execute('REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
    [JSON.stringify({
      received_at: new Date(Date.now() - 2 * 3600 * 1000).toISOString(),  // 2h old
      circuit_breaker: { live_mode: true, live_unavailable: false, equity: 128.99 },
    })]);
  // Also age out the close-driven snapshot path by clearing snapshots.
  await pool.execute('DELETE FROM equity_snapshots WHERE user_id = ?', [1]);
  const p = await request('GET', '/api/portfolio', { token: opToken });
  assert.strictEqual(p.data.equity, null, 'stale reading must not be presented as live');
  assert.strictEqual(p.data.live_unavailable, true);
});
