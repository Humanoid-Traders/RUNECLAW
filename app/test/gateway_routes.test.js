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
let mockLiveAllowed = false; // what /gateway/trade/live_mode reports (per-test)
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
        if (req.url === '/gateway/chat/public') {
          res.end(JSON.stringify({ reply_html: 'public pong', intent: 'chat' }));
        } else if (req.url === '/gateway/chat') {
          res.end(JSON.stringify({ reply_html: 'pong', intent: 'chat' }));
        } else if (req.url.startsWith('/gateway/chat/history')) {
          res.end(JSON.stringify({ messages: [] }));
        } else if (req.url === '/gateway/trade/propose') {
          res.end(JSON.stringify({ pending_trade: { trade_id: 'TI-test1234', mode: 'PAPER' } }));
        } else if (req.url.startsWith('/gateway/trade/live_mode')) {
          // Authoritative live-capability the confirm route consults to decide
          // whether a 2FA step-up is required. Flipped per-test via mockLiveAllowed.
          res.end(JSON.stringify({
            mode: mockLiveAllowed ? 'LIVE' : 'PAPER', live_allowed: mockLiveAllowed }));
        } else if (req.url === '/gateway/trade/confirm') {
          res.statusCode = 403;
          res.end(JSON.stringify({ error: 'not_proposer' }));
        } else if (req.url.startsWith('/gateway/portfolio')) {
          res.end(JSON.stringify({
            mode: 'PAPER', equity: 10000, balance: 9500, total_pnl: 12.5,
            daily_pnl: 0, win_rate: 100, total_trades: 1,
            open_positions: [{ symbol: 'SOL/USDT:USDT', direction: 'LONG', entry_price: 71, size_usd: 100, commission: 0.1, stop_loss: 70, take_profit: 76, opened_at: '2026-07-14T00:00:00Z' }],
            closed_trades: [{ symbol: 'ETH/USDT:USDT', direction: 'SHORT', entry_price: 1721, exit_price: 1642, size_usd: 200, pnl: 12.5, commission: 0.2, opened_at: '2026-07-13T00:00:00Z', closed_at: '2026-07-13T06:00:00Z' }],
            updated_at: '2026-07-14T01:00:00Z',
          }));
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

let jwt, pool, signLinked, signUnlinked, unlinkedId;

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
  unlinkedId = unlinked.id;

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/chat', require('../routes/chat'));
  app.use('/api/public/chat', require('../routes/public_chat'));
  app.use('/api/trade', require('../routes/webtrade'));
  app.use('/api/portfolio', require('../routes/portfolio'));
  app.use('/api/controls', require('../routes/controls'));
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

test('unlinked chat forwards web:<uid> identity (no 409)', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/chat', { token: signUnlinked, body: { text: 'hi' } });
  assert.strictEqual(r.status, 200);
  assert.match(seen[0].body.telegram_id, /^web:\d+$/);
  assert.strictEqual(seen[0].body.name, 'unlinked');
});

test('WEB-VISION: chat forwards validated images to the gateway', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/chat', {
    token: signUnlinked,
    body: { text: 'what is this?', images: [{ media_type: 'image/jpeg', data: 'QUJD' }] },
  });
  assert.strictEqual(r.status, 200);
  const chatCall = seen.find(s => s.url === '/gateway/chat');
  assert.ok(chatCall, 'gateway /chat was called');
  assert.strictEqual(chatCall.body.images.length, 1);
  assert.strictEqual(chatCall.body.images[0].media_type, 'image/jpeg');
  assert.strictEqual(chatCall.body.images[0].data, 'QUJD');
});

test('WEB-VISION: an image with no text is accepted (not 400)', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/chat', {
    token: signUnlinked,
    body: { text: '', images: [{ media_type: 'image/png', data: 'QUJD' }] },
  });
  assert.strictEqual(r.status, 200);
});

test('WEB-VISION: a bad media type is filtered out; no image forwarded', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/chat', {
    token: signUnlinked,
    body: { text: 'hi', images: [{ media_type: 'application/pdf', data: 'QUJD' }] },
  });
  assert.strictEqual(r.status, 200);
  const chatCall = seen.find(s => s.url === '/gateway/chat');
  assert.ok(chatCall, 'gateway /chat was called');
  // The invalid image is dropped, so no images key is forwarded.
  assert.strictEqual(chatCall.body.images, undefined);
});

test('unlinked trade propose forwards web:<uid> identity', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/trade/propose', {
    token: signUnlinked,
    body: { direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 200);
  assert.match(seen[0].body.telegram_id, /^web:\d+$/);
  // Order type defaults to limit (the platform's maker-only default).
  assert.strictEqual(seen[0].body.order_type, 'limit');
});

test('trade propose forwards an explicit market order type; garbage → limit', async () => {
  seen.length = 0;
  await request('POST', '/api/trade/propose', {
    token: signUnlinked,
    body: { direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76, order_type: 'market' },
  });
  assert.strictEqual(seen[0].body.order_type, 'market');
  seen.length = 0;
  await request('POST', '/api/trade/propose', {
    token: signUnlinked,
    body: { direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76, order_type: 'banana' },
  });
  assert.strictEqual(seen[0].body.order_type, 'limit');
});

test('controls stay telegram-gated for unlinked users', async () => {
  const r = await request('POST', '/api/controls', {
    token: signUnlinked, body: { paused: true },
  });
  assert.strictEqual(r.status, 409);
  assert.strictEqual(r.data.error, 'telegram_required');
});

test('portfolio proxies gateway and write-throughs to DB', async () => {
  seen.length = 0;
  const r = await request('GET', '/api/portfolio', { token: signUnlinked });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.equity, 10000);
  assert.strictEqual(r.data.stale, false);
  assert.match(seen[0].url, /^\/gateway\/portfolio\?telegram_id=web%3A\d+$/);
  // Write-through: snapshot + closed + open rows landed under the JWT user
  const [snaps] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1', [unlinkedId]);
  assert.strictEqual(parseFloat(snaps[0].equity), 10000);
  const [open] = await pool.execute(
    "SELECT * FROM trades WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC", [unlinkedId]);
  assert.strictEqual(open.length, 1);
  assert.strictEqual(open[0].symbol, 'SOL/USDT:USDT');
  // Second call must not duplicate the closed trade (upsert by key)
  await request('GET', '/api/portfolio', { token: signUnlinked });
  const [closed] = await pool.execute(
    "SELECT symbol, closed_at, pnl FROM trades WHERE user_id = ? AND status = 'CLOSED' ORDER BY closed_at DESC LIMIT ?", [unlinkedId, 500]);
  assert.strictEqual(closed.length, 1);
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

test('public chat works with NO auth and forwards only { text }', async () => {
  seen.length = 0;
  const r = await request('POST', '/api/public/chat', { body: { text: 'what is runeclaw?' } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.reply_html, 'public pong');
  assert.strictEqual(seen.length, 1);
  assert.strictEqual(seen[0].url, '/gateway/chat/public');
  assert.strictEqual(seen[0].secret, process.env.WEB_GATEWAY_SECRET);
  // No identity crosses to the bot — the body carries ONLY the text.
  assert.deepStrictEqual(seen[0].body, { text: 'what is runeclaw?' });
  assert.strictEqual(seen[0].body.telegram_id, undefined);
});

test('public chat rejects empty and oversized text', async () => {
  let r = await request('POST', '/api/public/chat', { body: {} });
  assert.strictEqual(r.status, 400);
  r = await request('POST', '/api/public/chat', { body: { text: 'x'.repeat(2001) } });
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

// AUDIT-FIX-3: the confirm 2FA step-up must key off the bot's AUTHORITATIVE
// live capability (/trade/live_mode), not the removed user_controls.live_enabled
// mirror — which was empty for Telegram-`/live` users and web-only live users,
// letting their live confirms skip the code. Enroll TOTP on the linked user and
// verify: (a) live-capable + no code => blocked, gateway confirm NOT reached;
// (b) not live-capable => step-up skipped, confirm proceeds to the gateway.
test('2FA step-up on live confirm keys off gateway live capability, not a stale mirror', async () => {
  const totp = require('../lib/totp');
  const secret = totp.generateSecret();
  // Enroll 2FA directly on the linked user's row (MemoryDB has no TOTP DDL path).
  const [lr] = await pool.execute('SELECT * FROM users WHERE telegram_id = ?', ['777']);
  lr[0].totp_enabled = 1;
  lr[0].totp_secret = secret;

  // (a) Gateway reports LIVE-capable + no code supplied => 401 step-up, and the
  // gateway confirm endpoint is never called.
  mockLiveAllowed = true;
  seen.length = 0;
  let r = await request('POST', '/api/trade/confirm', {
    token: signLinked, body: { trade_id: 'TI-test1234' },
  });
  assert.strictEqual(r.status, 401);
  assert.strictEqual(r.data.error, 'two_factor_required');
  assert.ok(seen.some(s => s.url.startsWith('/gateway/trade/live_mode')), 'live_mode was consulted');
  assert.ok(!seen.some(s => s.url === '/gateway/trade/confirm'), 'no live confirm without a code');

  // (b) A fresh valid code passes the step-up and the confirm reaches the gateway.
  const code = totp.hotp(secret, Math.floor(Date.now() / 30000));
  seen.length = 0;
  r = await request('POST', '/api/trade/confirm', {
    token: signLinked, body: { trade_id: 'TI-test1234', totp_code: code },
  });
  assert.strictEqual(r.status, 403);           // downstream not_proposer (expected)
  assert.strictEqual(r.data.error, 'not_proposer');
  assert.ok(seen.some(s => s.url === '/gateway/trade/confirm'), 'valid code => confirm forwarded');
});

test('2FA step-up is skipped for a non-live-capable (paper) confirm — one-tap paper stays frictionless', async () => {
  const [lr] = await pool.execute('SELECT * FROM users WHERE telegram_id = ?', ['777']);
  lr[0].totp_enabled = 1;                       // 2FA enrolled…
  // …but the gateway reports PAPER (not live-capable) => no code required.
  mockLiveAllowed = false;
  seen.length = 0;
  const r = await request('POST', '/api/trade/confirm', {
    token: signLinked, body: { trade_id: 'TI-test1234' },   // no totp_code
  });
  assert.strictEqual(r.status, 403);            // reached the gateway (not blocked)
  assert.strictEqual(r.data.error, 'not_proposer');
  assert.ok(seen.some(s => s.url === '/gateway/trade/confirm'), 'paper confirm forwarded without a code');

  // Un-enroll so the shared user row doesn't force step-up in later tests.
  lr[0].totp_enabled = 0; lr[0].totp_secret = null;
  mockLiveAllowed = false;
});

test('trade requires JWT', async () => {
  const r = await request('POST', '/api/trade/propose', {
    body: { direction: 'LONG', symbol: 'SOL', entry: 71, sl: 70, tp: 76 },
  });
  assert.strictEqual(r.status, 401);
});

test('operator portfolio uses sync data: no gateway call, no paper write-through, LIVE mode', async () => {
  // The linked user was created first -> id 1 === default BOT_USER_ID.
  // Seed the bot-sync state: a live-mode scan payload + a live equity snapshot.
  await pool.execute('REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)',
    [JSON.stringify({ circuit_breaker: { live_mode: true } })]);
  await pool.execute(
    'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
    [1, 8829.96, new Date()]);
  seen.length = 0;
  const r = await request('GET', '/api/portfolio', { token: signLinked });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.source, 'sync');
  assert.strictEqual(r.data.mode, 'LIVE');
  assert.strictEqual(r.data.equity, 8829.96);
  assert.strictEqual(r.data.stale, false);
  assert.strictEqual(seen.length, 0); // gateway NEVER consulted for the operator
  // And no paper snapshot was written through on top of the live one.
  const [snaps] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1', [1]);
  assert.strictEqual(parseFloat(snaps[0].equity), 8829.96);
});
