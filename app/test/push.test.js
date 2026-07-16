'use strict';
/**
 * Web push: opt-in subscriptions, endpoint-scoped unsubscribe, ingest-driven
 * sends (trades + non-info alerts only — never scans/theses), and automatic
 * pruning of subscriptions the push service reports as gone.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

// VAPID keys must exist BEFORE lib/push is required (it configures at import).
const webpush = require('web-push');
const vapid = webpush.generateVAPIDKeys();
process.env.VAPID_PUBLIC_KEY = vapid.publicKey;
process.env.VAPID_PRIVATE_KEY = vapid.privateKey;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const push = require('../lib/push');

let server, base;
const sends = [];   // captured by the injected fake transport
let failWith = null;

test.before(async () => {
  push.setSender(async (subscription, payload) => {
    if (failWith) { const e = new Error('gone'); e.statusCode = failWith; throw e; }
    sends.push({ endpoint: subscription.endpoint, payload: JSON.parse(payload) });
  });
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/push', require('../routes/push'));
  app.use('/api/bot/sync', require('../routes/sync'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function req(method, path, { token, botSecret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
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

const SUB = (n) => ({ endpoint: `https://push.example/${n}`,
  keys: { p256dh: 'p'.repeat(20), auth: 'a'.repeat(10) } });
const settle = () => new Promise(res => setTimeout(res, 80));

test('subscribe/unsubscribe: authed, validated, endpoint-scoped', async () => {
  let r = await req('GET', '/api/push/key');
  assert.strictEqual(r.status, 401);

  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'push1@test.io', password: 'x'.repeat(12) } });
  const token = reg.data.token;

  r = await req('GET', '/api/push/key', { token });
  assert.strictEqual(r.data.enabled, true);
  assert.strictEqual(r.data.public_key, process.env.VAPID_PUBLIC_KEY);
  assert.strictEqual(r.data.subscribed, 0);

  // http:// endpoint and missing keys are rejected.
  r = await req('POST', '/api/push/subscribe', { token,
    body: { subscription: { endpoint: 'http://evil', keys: {} } } });
  assert.strictEqual(r.status, 400);

  r = await req('POST', '/api/push/subscribe', { token, body: { subscription: SUB(1) } });
  assert.strictEqual(r.status, 200);
  // Same endpoint again upserts — still one subscription.
  await req('POST', '/api/push/subscribe', { token, body: { subscription: SUB(1) } });
  r = await req('GET', '/api/push/key', { token });
  assert.strictEqual(r.data.subscribed, 1);

  r = await req('POST', '/api/push/unsubscribe', { token, body: { endpoint: SUB(1).endpoint } });
  assert.strictEqual(r.status, 200);
  r = await req('GET', '/api/push/key', { token });
  assert.strictEqual(r.data.subscribed, 0);
});

test('bot event ingest pushes trades + warnings, never scans; 410 prunes', async () => {
  const reg = await req('POST', '/api/auth/register',
    { body: { email: 'push2@test.io', password: 'x'.repeat(12) } });
  const token = reg.data.token;
  await req('POST', '/api/push/subscribe', { token, body: { subscription: SUB(2) } });

  sends.length = 0;
  await req('POST', '/api/bot/sync/events', {
    botSecret: process.env.BOT_SYNC_SECRET,
    body: { events: [
      { event_type: 'scan', title: 'Scan complete — no push for this' },
      { event_type: 'trade_close', severity: 'success', title: 'Closed SOL +$2.41', body: 'Exit: TP1' },
      { event_type: 'alert', severity: 'info', title: 'info alert — no push' },
      { event_type: 'alert', severity: 'warning', title: 'Circuit breaker tripped' },
    ] },
  });
  await settle();
  const titles = sends.map(s => s.payload.title).sort();
  assert.deepStrictEqual(titles, [
    'RUNECLAW — Circuit breaker tripped',
    'RUNECLAW — Closed SOL +$2.41',
  ]);
  assert.ok(sends.every(s => s.payload.url === '/dashboard#feed'));

  // Push service says the subscription is gone -> pruned, next send is a no-op.
  failWith = 410;
  await req('POST', '/api/bot/sync/events', {
    botSecret: process.env.BOT_SYNC_SECRET,
    body: { events: [{ event_type: 'trade_open', title: 'Opened LONG BTC' }] },
  });
  await settle();
  failWith = null;
  const k = await req('GET', '/api/push/key', { token });
  assert.strictEqual(k.data.subscribed, 0);
});
