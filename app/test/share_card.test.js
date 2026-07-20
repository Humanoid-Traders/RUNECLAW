'use strict';
/**
 * Share-card relay — /api/share/card returns the bot-rendered PNG for the
 * closed-trade share flow. The card is a pure function of (symbol, direction,
 * PnL percent): JWT required, inputs validated hard, binary relayed only when
 * the gateway really returned a PNG, and gateway-down degrades to JSON errors
 * so the client's text-only share path still works.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

// Stub the gateway module BEFORE the route requires it. A tiny fake PNG is
// enough — the route only checks status + content type and relays bytes.
const FAKE_PNG = Buffer.concat([
  Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
  Buffer.from('fake-png-body'),
]);
const gateway = require('../lib/gateway');
let gatewayResponse = { status: 200, contentType: 'image/png', body: FAKE_PNG };
let lastGatewayPath = null;
gateway.getGatewayBinary = async (p) => { lastGatewayPath = p; return gatewayResponse; };

const authModule = require('../auth');

let server, base, token;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/share', require('../routes/share'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
  token = await newUser();
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
      const chunks = [];
      res.on('data', c => chunks.push(c));
      res.on('end', () => resolve({
        status: res.statusCode,
        type: res.headers['content-type'] || '',
        raw: Buffer.concat(chunks),
      }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

async function newUser() {
  const r = await req('POST', '/api/auth/register', {
    body: { email: 'share1@example.com', password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return JSON.parse(r.raw.toString()).token;
}

test('unauthenticated request is rejected', async () => {
  const r = await req('GET', '/api/share/card?symbol=BTC&direction=LONG&pnl_pct=1');
  assert.equal(r.status, 401);
});

test('valid request relays the PNG with image content type', async () => {
  const r = await req('GET', '/api/share/card?symbol=BTC&direction=LONG&pnl_pct=4.2', { token });
  assert.equal(r.status, 200);
  assert.match(r.type, /image\/png/);
  assert.ok(r.raw.equals(FAKE_PNG), 'PNG bytes relayed unmodified');
  assert.match(lastGatewayPath, /^\/share-card\?/, 'hits the gateway share-card path');
  assert.match(lastGatewayPath, /pnl_pct=4\.20/, 'percent normalized to 2dp');
});

test('input validation: symbol, direction, pnl_pct', async () => {
  const bad = [
    'symbol=BTC%2FUSDT&direction=LONG&pnl_pct=1',  // slash rejected
    'symbol=BTC&direction=SIDEWAYS&pnl_pct=1',
    'symbol=BTC&direction=LONG&pnl_pct=abc',
    'symbol=BTC&direction=LONG&pnl_pct=Infinity',
    'symbol=&direction=LONG&pnl_pct=1',
    'symbol=AVERYLONGSYMBOLNAME&direction=LONG&pnl_pct=1',
  ];
  for (const qs of bad) {
    const r = await req('GET', `/api/share/card?${qs}`, { token });
    assert.equal(r.status, 400, `expected 400 for ${qs}`);
  }
});

test('non-PNG or failed gateway response becomes 502 JSON, never relayed', async () => {
  gatewayResponse = { status: 200, contentType: 'application/json', body: Buffer.from('{}') };
  let r = await req('GET', '/api/share/card?symbol=BTC&direction=LONG&pnl_pct=1', { token });
  assert.equal(r.status, 502);

  gatewayResponse = { status: 503, contentType: 'image/png', body: FAKE_PNG };
  r = await req('GET', '/api/share/card?symbol=BTC&direction=LONG&pnl_pct=1', { token });
  assert.equal(r.status, 502);

  gatewayResponse = { status: 200, contentType: 'image/png', body: FAKE_PNG };
});

test('privacy: the route accepts no dollar/size parameters', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'share.js'), 'utf8');
  for (const leak of ['pnl_usd', 'size_usd', 'margin', 'equity', 'net_pnl']) {
    assert.ok(!src.includes(leak), `share route must never touch ${leak}`);
  }
});
