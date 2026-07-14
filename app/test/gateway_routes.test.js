/**
 * Web chat + web trade proxy routes (routes/chat.js, routes/webtrade.js).
 *
 * Spins up: (1) a mock bot gateway on an ephemeral port that records requests,
 * (2) an express app mounting the real routers against the MemoryDB fallback.
 * Pins: JWT required, Telegram link required (409), server-side telegram_id
 * injection, 4xx pass-through, and 503 when the gateway is unconfigured.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
// BOT_GATEWAY_URL is set after the mock server binds (see before()).

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const seen = []; // requests the mock gateway received
let mockGateway;
let appServer;
let base;

// Mock bot gateway: /gateway/chat echoes, /gateway/trade/propose returns a
// pending trade, /gateway/trade/confirm 403s (proposer isolation downstream).
function startMockGateway() {
  return new Promise((resolve) => {
    mockGateway = http.createServer((req, res) => {
      let body = '';
      req.on('data', d => body += d);
      req.on('end', () => {
        const record = {
          url: req.url,
          method: req.method,
          secret: req.headers['x-gateway-secret'],
          body: body ? JSON.parse(body) : null,
        };
        seen.push(record);
        res.setHeader('Content-Type', 'application/json');
        if (req.url === '/gateway/chat') {
          res.end(JSON.stringify({ reply_html: 'pong', intent: 'chat' }));
        } else if (req.url.startsWith('/gateway/chat/history')) {
          res.end(JSON.stringify({ messages: [] }));
        } else if (req.url === '/gateway/trade/propose') {
          res.end(JSON.stringify({ pending_trade: { trade_id: 'TI-test1234', mode: 'PAPER' } }));
        } else if (req.url === '/gateway/trade/confirm') {
          res.statusCode = 403;
          res.end(JSON.stringify({ error: 'not_proposer' }));
        } else {
          res.end(JSON.stringify({ ok: true }));
        }
      });
    });
    mockGateway.listen(0, '127.0.0.1', () => resolve(mockGateway.address().port));
  });
}

function request(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const req = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve({ status: res.statusCode, data: data ? JSON.parse(data) : {} }));
    });
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

let jwt, pool, signLinked, signUnlinked;

test.before(async () => {
  const gwPort = await startMockGateway();
  process.env.BOT_GATEWAY_URL = `http://127.0.0.1:${gwPort}`;

  jwt = require('jsonwebtoken');
  const db = require('../db');
  pool = db.pool;

  // Seed two users in the MemoryDB: one Telegram-linked, one not.
  await pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['linked@test.io', 'x', 'Linked']);
  await pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['unlinked@test.io', 'x', 'Unlinked']);
  const [rows] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['linked@test.io']);
  const linked = rows[0];
  const [rows2] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['unlinked@test.io']);
  const unlinked = rows2[0];
  await pool.execute('UPDATE users SET telegram_id = ? WHERE id = ?', ['777', linked.id]);
  // MemoryDB's UPDATE...TELEGRAM_ID sets telegram_id but not telegram_linked; set it directly.
  const [lrows] = await pool.execute('SELECT * FROM users WHERE id = ?', [linked.id]);
  lrows[0].telegram_linked = true;

  signLinked = jwt.sign({ user_id: linked.id, email: linked.email }, process.env.JWT_SECRET);
  signUnlinked = jwt.sign({ user_id: unlinked.id, email: unlinked.email }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/chat', require('../routes/chat'));
  app.use('/api/trade', require('../routes/webtrade'));
  await new Promise((resolve) => {
    appServer = app.listen(0, '127.0.0.1', resolve);
  });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => {
  if (appServer) appServer.close();
  if (mockGateway) mockGateway.close();
});

test('chat requires JWT', async () => {
  const r = await request('POST', '/api/chat', { body: { text: 'hi' } });
  assert.strictEqual(r.status, 401);
});

test('chat 409s for unlinked telegram', async () => {
  const r = await request('POST', '/api/chat', { token: signUnlinked, body: { text: 'hi' } });
  assert.strictEqual(r.status, 409);
});

test('chat forwards with server-side telegram_id and secret', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/chat', { token: signLinked, body: { text: 'hi bot' } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.reply_html, 'pong');
  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].url, '/gateway/chat');
  assert.strictEqual(seen[0].secret, process.env.WEB_GATEWAY_SECRET);
  assert.strictEqual(seen[0].body.telegram_id, '777');
  assert.strictEqual(seen[0].body.name, 'linked');
});

test('chat rejects empty text', async () => {
  const r = await request('POST', '/api/chat', { token: signLinked, body: {} });
  assert.strictEqual(r.status, 400);
});

test('client-sent telegram_id is ignored', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/trade/propose', {
    token: signLinked,
    body: { telegram_id: '999', direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.pending_trade.trade_id, 'TI-test1234');
  assert.strictEqual(seen[0].body.telegram_id, '777'); // NOT 999
});

test('propose validates direction and numbers', async () => {
  let r = await request('POST', '/api/trade/propose', {
    token: signLinked,
    body: { direction: 'UP', symbol: 'SOL', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 400);
  r = await request('POST', '/api/trade/propose', {
    token: signLinked,
    body: { direction: 'LONG', symbol: 'SOL', entry: 'abc', sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 400);
  r = await request('POST', '/api/trade/propose', {
    token: signLinked,
    body: { direction: 'LONG', symbol: 'bad symbol!', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 400);
});

test('confirm passes 4xx through from the gateway', async () => {
  const r = await request('POST', '/api/trade/confirm', {
    token: signLinked, body: { trade_id: 'TI-test1234' },
  });
  assert.strictEqual(r.status, 403);
  assert.strictEqual(r.data.error, 'not_proposer');
});

test('trade requires JWT', async () => {
  const r = await request('POST', '/api/trade/propose', {
    body: { direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 401);
});
