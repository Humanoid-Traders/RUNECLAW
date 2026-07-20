'use strict';
/**
 * Public agent directory relay (/api/public/agent/:address) — no auth, hard
 * address validation before anything reaches the gateway, per-address cache
 * for both 200 and 404 (an unknown-agent probe can't hammer the bot), and the
 * /agent/:address page wiring.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.WEB_GATEWAY_SECRET = 'g'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('node:fs');
const path = require('node:path');

const gateway = require('../lib/gateway');
const calls = [];
gateway.getGateway = async (p) => {
  calls.push(p);
  if (p.includes('cdcdcd')) return { status: 404, data: { error: 'unknown_agent' } };
  return { status: 200, data: { card: { card_hash: 'h' }, verified: true } };
};

let server, base;

test.before(async () => {
  const app = express();
  app.use('/api/public/agent', require('../routes/public_agent'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function get(p) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method: 'GET' }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

const GOOD = '0x' + 'ab'.repeat(20);
const MISSING = '0x' + 'cd'.repeat(10) + 'cd'.repeat(10);

test('valid address relays; invalid never reaches the gateway', async () => {
  let r = await get(`/api/public/agent/${GOOD}`);
  assert.equal(r.status, 200);
  assert.equal(r.data.verified, true);

  const n = calls.length;
  for (const bad of ['0xZZ', 'abab', `/api/../${GOOD}`, '0x' + 'ab'.repeat(19)]) {
    const rr = await get(`/api/public/agent/${encodeURIComponent(bad)}`);
    assert.equal(rr.status, 400, bad);
  }
  assert.equal(calls.length, n, 'invalid addresses never hit the gateway');
});

test('unknown agent 404 is relayed and cached', async () => {
  let r = await get(`/api/public/agent/${MISSING}`);
  assert.equal(r.status, 404);
  const n = calls.length;
  r = await get(`/api/public/agent/${MISSING}`);
  assert.equal(r.status, 404);
  assert.equal(calls.length, n, '404 served from cache — probes cannot hammer the bot');
});

test('mixed-case addresses normalize to one cache key', async () => {
  const n = calls.length;
  await get(`/api/public/agent/${GOOD.toUpperCase().replace('0X', '0x')}`);
  assert.equal(calls.length, n, 'uppercase variant hits the lowercase cache entry');
});

test('the /agent/:address page carries the card wiring', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'agent-card.html'), 'utf8');
  assert.match(html, /\/api\/public\/agent\//);
  assert.match(html, /UNVERIFIED/, 'anchor honesty is stated on the page');
  assert.match(html, /re-verified|verification failed/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.get\('\/agent\/:address'/);
  assert.match(server, /routes\/public_agent/);
});
