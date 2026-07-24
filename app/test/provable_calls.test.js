'use strict';
/**
 * Provable Calls — pre-commitment receipts for engine signals. Sealed at
 * decision time from decision-time facts ONLY; outcomes attach later
 * without touching the seal; anyone re-derives the hash in their browser.
 * "Don't trust the screenshot. Verify the call."
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const crypto = require('node:crypto');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

const { sealCall, sealOf, canonicalPayload } = require('../lib/callseal');
const { pool } = require('../db');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/call', require('../routes/call'));
  app.use('/api/signals', require('../routes/signals'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

function req(method, p, { body, headers } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: { ...(payload ? { 'Content-Type': 'application/json' } : {}), ...(headers || {}) },
    }, (res) => {
      let d = ''; res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
const BOT = { 'X-Bot-Secret': process.env.BOT_SYNC_SECRET };

const CALL = {
  signal_key: 'sig:test:0001', symbol: 'BTCUSDT', direction: 'LONG',
  confidence: 0.72, score: 1.1, pattern: 'Bull Flag', regime: 'TREND_UP',
  entry_price: 63500.5, stop_loss: 62800, take_profit: 65200, rr: 2.4,
  thesis: 't', status: 'NEW', created_at: '2026-07-25T10:00:00.000Z',
};

test('the seal covers decision facts only — outcomes cannot move it', () => {
  const a = sealCall(CALL);
  assert.equal(a.seal.length, 64);
  assert.equal(sealOf(a.seal_payload), a.seal, 'self-consistent');
  // Outcome-ish fields are not part of the payload at all.
  const b = sealCall({ ...CALL, status: 'WIN', pnl: 999, resolved_at: new Date() });
  assert.equal(b.seal, a.seal, 'status/pnl/resolved_at never affect the seal');
  // Any decision fact DOES move it.
  assert.notEqual(sealCall({ ...CALL, entry_price: 63500.6 }).seal, a.seal);
  assert.notEqual(sealCall({ ...CALL, direction: 'SHORT' }).seal, a.seal);
  // Canonical payload is stable JSON with the version marker.
  assert.match(canonicalPayload(CALL), /^\{"v":1,"signal_key":/);
});

test('sync seals a NEW call; resolution re-sync leaves the receipt untouched', async () => {
  const r1 = await req('POST', '/api/bot/sync/signals', { headers: BOT, body: { signals: [CALL] } });
  assert.equal(r1.status, 200);
  const row1 = pool.signals.find((s) => s.signal_key === CALL.signal_key);
  assert.ok(row1.seal && row1.seal_payload && row1.sealed_at, 'sealed on insert');
  assert.equal(sealOf(row1.seal_payload), row1.seal);
  // The engine resolves the call — outcome fields update, receipt does not.
  const resolved = { ...CALL, status: 'WIN', pnl: 420.5, resolved_at: '2026-07-25T14:00:00.000Z' };
  await req('POST', '/api/bot/sync/signals', { headers: BOT, body: { signals: [resolved] } });
  const row2 = pool.signals.find((s) => s.signal_key === CALL.signal_key);
  assert.equal(row2.seal, row1.seal, 'seal survives resolution');
  assert.equal(row2.seal_payload, row1.seal_payload, 'payload survives resolution');
  assert.equal(Number(row2.pnl), 420.5, 'outcome attached');
});

test('/api/call serves the receipt; the browser-side verify math holds', async () => {
  const r = await req('GET', '/api/call/' + encodeURIComponent(CALL.signal_key));
  assert.equal(r.status, 200);
  const d = r.data;
  // What call.html does with WebCrypto, mirrored with node crypto:
  const hex = crypto.createHash('sha256').update(d.seal_payload, 'utf8').digest('hex');
  assert.equal(hex, d.seal, 'sha256(seal_payload) === seal');
  const p = JSON.parse(d.seal_payload);
  assert.equal(p.symbol, 'BTCUSDT');
  assert.equal(p.entry_price, 63500.5);
  // No drift between sealed payload and the live record.
  for (const f of ['symbol', 'direction', 'entry_price', 'stop_loss', 'take_profit']) {
    assert.deepEqual(p[f], d.current[f], `no drift on ${f}`);
  }
  assert.equal(Number(d.outcome.pnl), 420.5);
});

test('tampering with the live row becomes VISIBLE through the receipt', async () => {
  const row = pool.signals.find((s) => s.signal_key === CALL.signal_key);
  const original = row.entry_price;
  row.entry_price = 63000;                        // a rewrite attempt
  const r = await req('GET', '/api/call/' + encodeURIComponent(CALL.signal_key));
  const p = JSON.parse(r.data.seal_payload);
  assert.notEqual(Number(r.data.current.entry_price), p.entry_price,
    'the drift the verify page flags in red');
  assert.equal(crypto.createHash('sha256').update(r.data.seal_payload, 'utf8').digest('hex'),
    r.data.seal, 'the sealed payload itself is still intact');
  row.entry_price = original;
});

test('unknown / unsealed calls 404 honestly; junk ids 400', async () => {
  assert.equal((await req('GET', '/api/call/sig:nope')).status, 404);
  assert.equal((await req('GET', '/api/call/%20%20')).status, 400);
});

test('wiring: page served, WebCrypto verify, honest explainer, signal-row links', () => {
  const page = fs.readFileSync(path.join(__dirname, '..', 'public', 'call.html'), 'utf8');
  assert.match(page, /crypto\.subtle\.digest\('SHA-256'/);
  assert.match(page, /What it doesn.t prove yet/);
  assert.match(page, /on-chain/);
  assert.match(page, /HASH MISMATCH/);
  assert.match(page, /The live record DIFFERS from what was sealed/);
  const server_ = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_, /app\.get\('\/call\/:key'/);
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /\/call\/\$\{encodeURIComponent\(s\.signal_key\)\}/);
  assert.match(dash, /s\.seal \?/);
});
