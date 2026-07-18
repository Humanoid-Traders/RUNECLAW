'use strict';
/**
 * Custom agent alerts ("tell me when…" tripwires): plain-English parsing,
 * validated creation with a per-user cap, strict per-user isolation,
 * one-shot engine evaluation against tickers, TARGETED push delivery, and
 * the chat intercept that arms alerts without a bot round-trip.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;   // chat proxy must be unconfigured here

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const alerts = require('../lib/alerts');

let server, base;

// Deterministic ticker source for every test.
const TICKERS = {
  BTCUSDT: { price: 98_000, change: -2.5 },
  SOLUSDT: { price: 150, change: 6.2 },
  ETHUSDT: { price: 2_500, change: 0.4 },
};

test.before(async () => {
  alerts.setTickerFetcher(async () => TICKERS);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/alerts', require('../routes/alerts'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
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

let userSeq = 0;
async function newUser() {
  userSeq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `alerts${userSeq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

// ── Parser ───────────────────────────────────────────────────────────────────

test('parser: price below with $ and k-suffix', () => {
  const p = alerts.parseAlertCommand('tell me when BTC drops below $100k');
  assert.deepEqual(p, { kind: 'create', base: 'BTC', metric: 'price', op: '<', threshold: 100_000, mode: 'once' });
});

test('parser: coin name + above', () => {
  const p = alerts.parseAlertCommand('alert me if solana rises above 200');
  assert.equal(p.base, 'SOL');
  assert.equal(p.op, '>');
  assert.equal(p.threshold, 200);
});

test('parser: "hits" defers direction to the live price', () => {
  const p = alerts.parseAlertCommand('let me know when eth hits 3000');
  assert.equal(p.base, 'ETH');
  assert.equal(p.metric, 'price');
  assert.equal(p.inferOp, true);
});

test('parser: percent drop → signed 24h-change condition', () => {
  const p = alerts.parseAlertCommand('tell me when BTC drops 5%');
  assert.equal(p.metric, 'change_24h');
  assert.equal(p.op, '<');
  assert.equal(p.threshold, -5);
});

test('parser: "moves X%" → absolute 24h-change condition', () => {
  const p = alerts.parseAlertCommand('alert me if SOL moves 8%');
  assert.equal(p.metric, 'change_abs_24h');
  assert.equal(p.op, '>');
  assert.equal(p.threshold, 8);
});

test('parser: list + non-alert text', () => {
  assert.deepEqual(alerts.parseAlertCommand('my alerts'), { kind: 'list' });
  assert.deepEqual(alerts.parseAlertCommand('show my alerts'), { kind: 'list' });
  assert.equal(alerts.parseAlertCommand('what is a liquidity sweep?'), null);
  assert.equal(alerts.parseAlertCommand('tell me about bitcoin'), null);
  assert.equal(alerts.parseAlertCommand('tell me when btc looks bullish').kind, 'unparsed');
});

// ── REST: auth, validation, isolation, cap ───────────────────────────────────

test('REST: unauthenticated is rejected', async () => {
  const r = await req('GET', '/api/alerts');
  assert.equal(r.status, 401);
});

test('REST: create + list + delete, scoped to the caller', async () => {
  const tokenA = await newUser();
  const tokenB = await newUser();

  const c = await req('POST', '/api/alerts', {
    token: tokenA,
    body: { symbol: 'BTC', metric: 'price', op: '>', threshold: 120000 },
  });
  assert.equal(c.status, 200);
  assert.match(c.data.label, /BTC price above/);
  assert.equal(c.data.now, 98_000);

  // B sees an empty list — never A's alerts.
  const lb = await req('GET', '/api/alerts', { token: tokenB });
  assert.equal(lb.data.alerts.length, 0);
  const la = await req('GET', '/api/alerts', { token: tokenA });
  assert.equal(la.data.alerts.length, 1);
  const id = la.data.alerts[0].id;

  // B cannot delete A's alert.
  const db = await req('DELETE', `/api/alerts/${id}`, { token: tokenB });
  assert.equal(db.status, 404);
  const da = await req('DELETE', `/api/alerts/${id}`, { token: tokenA });
  assert.equal(da.status, 200);
  const after = await req('GET', '/api/alerts', { token: tokenA });
  assert.equal(after.data.alerts.length, 0);
});

test('REST: unknown symbol is rejected with a plain-language error', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/alerts', {
    token, body: { symbol: 'NOPE', metric: 'price', op: '>', threshold: 1 },
  });
  assert.equal(r.status, 400);
  assert.match(r.data.error, /can't find/i);
});

test('REST: active-alert cap enforced', async () => {
  const token = await newUser();
  for (let i = 0; i < alerts.MAX_ACTIVE_PER_USER; i++) {
    const r = await req('POST', '/api/alerts', {
      token, body: { symbol: 'BTC', metric: 'price', op: '>', threshold: 200000 + i },
    });
    assert.equal(r.status, 200);
  }
  const over = await req('POST', '/api/alerts', {
    token, body: { symbol: 'BTC', metric: 'price', op: '>', threshold: 999999 },
  });
  assert.equal(over.status, 400);
  assert.match(over.data.error, /active alerts/);
});

// ── Engine: one-shot evaluation + TARGETED push ──────────────────────────────

test('engine: trips exactly once and pushes only to the owner', async () => {
  const token = await newUser();
  // BTC is at 98k; arm "below 99k" (already true → trips on next sweep).
  const c = await req('POST', '/api/alerts', {
    token, body: { symbol: 'BTC', metric: 'price', op: '<', threshold: 99000 },
  });
  assert.equal(c.status, 200);
  // …and one that should NOT trip.
  await req('POST', '/api/alerts', {
    token, body: { symbol: 'SOL', metric: 'price', op: '>', threshold: 500 },
  });

  const pushes = [];
  const notify = async (payload, userIds) => { pushes.push({ payload, userIds }); };
  const tripped = await alerts.runOnce(notify);
  assert.equal(tripped, 1);
  assert.equal(pushes.length, 1);
  assert.match(pushes[0].payload.title, /BTC alert tripped/);
  assert.match(pushes[0].payload.body, /98,000/);
  assert.equal(Array.isArray(pushes[0].userIds), true);
  assert.equal(pushes[0].userIds.length, 1);

  // One-shot: a second sweep does not re-fire.
  const again = await alerts.runOnce(notify);
  assert.equal(again, 0);
  assert.equal(pushes.length, 1);

  // The tripped alert is disarmed but retained (with the trigger price).
  const l = await req('GET', '/api/alerts', { token });
  const trippedRow = l.data.alerts.find(a => a.symbol === 'BTCUSDT');
  assert.equal(trippedRow.active, false);
  assert.equal(trippedRow.trigger_price, 98000);
  const solRow = l.data.alerts.find(a => a.symbol === 'SOLUSDT');
  assert.equal(solRow.active, true);
});

test('engine: 24h-change alerts evaluate against the change field', async () => {
  const token = await newUser();
  // SOL is +6.2% → "pumps 5%" trips, "drops 5%" does not.
  await req('POST', '/api/alerts', {
    token, body: { symbol: 'SOL', metric: 'change_24h', op: '>', threshold: 5 },
  });
  await req('POST', '/api/alerts', {
    token, body: { symbol: 'SOL', metric: 'change_24h', op: '<', threshold: -5 },
  });
  const pushes = [];
  const tripped = await alerts.runOnce(async (p, u) => pushes.push(p));
  assert.equal(tripped, 1);
  assert.match(pushes[0].body, /above 5\.0%/);
});

// ── Chat intercept ───────────────────────────────────────────────────────────

test('chat: "tell me when…" arms an alert without the bot', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', {
    token, body: { text: 'tell me when BTC drops below $90k' },
  });
  assert.equal(r.status, 200);
  assert.equal(r.data.intent, 'alert_create');
  assert.match(r.data.reply_html, /Alert armed/);
  assert.match(r.data.reply_html, /BTC price below \$90,000/);
  assert.match(r.data.reply_html, /now \$98,000/);
  // No push subscription yet → the reply nudges the user to enable push.
  assert.match(r.data.reply_html, /push notifications/i);

  const l = await req('GET', '/api/alerts', { token });
  assert.equal(l.data.alerts.length, 1);
  assert.equal(l.data.alerts[0].active, true);
});

test('chat: "my alerts" lists them; unparsed asks get guidance', async () => {
  const token = await newUser();
  await req('POST', '/api/chat', {
    token, body: { text: 'alert me if eth rises above 3k' },
  });
  const list = await req('POST', '/api/chat', { token, body: { text: 'my alerts' } });
  assert.equal(list.data.intent, 'alert_list');
  assert.match(list.data.reply_html, /ETH price above \$3,000/);
  assert.match(list.data.reply_html, /armed/);

  const help = await req('POST', '/api/chat', {
    token, body: { text: 'tell me when eth looks spicy' },
  });
  assert.equal(help.data.intent, 'alert_help');
});

test('chat: non-alert text still routes to the bot proxy (unconfigured → 503)', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', { token, body: { text: 'hello there' } });
  assert.equal(r.status, 503);
});
