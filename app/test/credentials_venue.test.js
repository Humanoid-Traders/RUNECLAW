/**
 * Venue-aware exchange-credential connect (routes/credentials.js + the venue
 * catalog + the sync ack that stamps exchange_status).
 *
 * Pins: a Hyperliquid connect stores an encrypted payload that decrypts to
 * {venue:'hyperliquid', wallet_address, agent_private_key} on the right
 * pending row; /status and the bot ack carry the venue through; Bitget is the
 * default and its 3-field validation still holds; an unknown venue / missing
 * field is rejected; /config advertises the venue catalog.
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
const credsCrypto = require('../lib/creds_crypto');

let server, base, token, tgUserId;

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

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['venue@test.io', 'x', 'V']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['venue@test.io']);
  tgUserId = rows[0].id;
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['9001', tgUserId]);
  const [u] = await pool.execute('SELECT * FROM users WHERE id = ?', [tgUserId]);
  u[0].telegram_linked = true; // MemoryDB: set the flag directly
  token = jwt.sign({ user_id: tgUserId, email: 'venue@test.io' }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/credentials', require('../routes/credentials'));
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/auth', require('../auth').router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('/config advertises all four connectable venues with field specs', async () => {
  const r = await request('GET', '/api/auth/config');
  const venues = r.data.venues || [];
  const ids = venues.map(v => v.id);
  for (const v of ['bitget', 'bybit', 'bingx', 'hyperliquid']) {
    assert.ok(ids.includes(v), `${v} missing from /config venues`);
  }
  const hl = venues.find(v => v.id === 'hyperliquid');
  assert.deepStrictEqual(hl.fields.map(f => f.key), ['wallet_address', 'agent_private_key']);
  const by = venues.find(v => v.id === 'bybit');
  assert.deepStrictEqual(by.fields.map(f => f.key), ['api_key', 'api_secret']);
});

test('a Bybit connect stores an encrypted key/secret payload carrying the venue', async () => {
  const r = await request('POST', '/api/credentials', {
    token, body: { venue: 'bybit', api_key: 'BYKEY' + 'k'.repeat(12), api_secret: 'BYSEC' + 's'.repeat(12) },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.venue, 'bybit');
  const [pend] = await pool.execute('SELECT exchange, encrypted_payload FROM pending_credentials WHERE user_id = ?', [tgUserId]);
  assert.strictEqual(pend[0].exchange, 'bybit');
  const decoded = credsCrypto.decryptJSON(pend[0].encrypted_payload);
  assert.strictEqual(decoded.venue, 'bybit');
  assert.strictEqual(decoded.api_key, 'BYKEY' + 'k'.repeat(12));
  assert.strictEqual(decoded.passphrase, undefined);      // no Bitget field
  assert.strictEqual(decoded.wallet_address, undefined);  // no HL field
});

test('a Hyperliquid connect stores an encrypted payload carrying the venue', async () => {
  const r = await request('POST', '/api/credentials', {
    token,
    body: { venue: 'hyperliquid', wallet_address: '0x' + 'a'.repeat(40), agent_private_key: '0x' + 'b'.repeat(64) },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.venue, 'hyperliquid');
  // The pending row is stamped with the venue and its payload decrypts to it.
  const [pend] = await pool.execute('SELECT exchange, encrypted_payload FROM pending_credentials WHERE user_id = ?', [tgUserId]);
  assert.strictEqual(pend[0].exchange, 'hyperliquid');
  const decoded = credsCrypto.decryptJSON(pend[0].encrypted_payload);
  assert.strictEqual(decoded.venue, 'hyperliquid');
  assert.strictEqual(decoded.wallet_address, '0x' + 'a'.repeat(40));
  assert.strictEqual(decoded.agent_private_key, '0x' + 'b'.repeat(64));
  // No Bitget fields leaked in.
  assert.strictEqual(decoded.api_key, undefined);
});

test('status reports the pending venue, then the connected venue after the ack', async () => {
  let s = await request('GET', '/api/credentials/status', { token });
  assert.strictEqual(s.data.pending, 'connect');
  assert.strictEqual(s.data.pending_venue, 'hyperliquid');
  // Bot acks the import → exchange_status carries the venue.
  const ack = await request('POST', '/api/bot/sync/credentials/ack', {
    secret: process.env.BOT_SYNC_SECRET,
    body: { acks: [{ user_id: tgUserId, action: 'connect', ok: true }] },
  });
  assert.strictEqual(ack.status, 200);
  s = await request('GET', '/api/credentials/status', { token });
  assert.strictEqual(s.data.connected, true);
  assert.strictEqual(s.data.venue, 'hyperliquid');
});

test('Bitget is the default and still validates its three fields', async () => {
  // Missing passphrase → 400.
  let r = await request('POST', '/api/credentials', {
    token, body: { api_key: 'k'.repeat(16), api_secret: 's'.repeat(16) },
  });
  assert.strictEqual(r.status, 400);
  // Full Bitget triple, no venue → defaults to bitget.
  r = await request('POST', '/api/credentials', {
    token, body: { api_key: 'k'.repeat(16), api_secret: 's'.repeat(16), passphrase: 'pp' },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.venue, 'bitget');
  const [pend] = await pool.execute('SELECT exchange FROM pending_credentials WHERE user_id = ?', [tgUserId]);
  assert.strictEqual(pend[0].exchange, 'bitget');
});

test('an unknown venue is rejected', async () => {
  const r = await request('POST', '/api/credentials', {
    token, body: { venue: 'ftx', api_key: 'x'.repeat(16) },
  });
  assert.strictEqual(r.status, 400);
});
