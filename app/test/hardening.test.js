'use strict';
/**
 * Security-hardening regressions (audit CC): the real server.js is booted as
 * a child process so headers, proxy trust, and MCP guards are asserted
 * end-to-end — not against a lookalike express app.
 */
const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const { spawn } = require('node:child_process');
const path = require('node:path');

const PORT = 3311;
const BASE = `http://127.0.0.1:${PORT}`;
let child;

function req(method, p, { headers = {}, body = null } = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${BASE}${p}`, { method, headers }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        data: (() => { try { return JSON.parse(d); } catch (e) { return d; } })(),
      }));
    });
    r.on('error', reject);
    if (body) r.write(body);
    r.end();
  });
}

async function waitUp(tries = 60) {
  for (let i = 0; i < tries; i++) {
    try { await req('GET', '/'); return; } catch (e) { /* not yet */ }
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error('server never came up');
}

test.before(async () => {
  child = spawn(process.execPath, ['server.js'], {
    cwd: path.join(__dirname, '..'),
    env: {
      ...process.env,
      PORT: String(PORT),
      JWT_SECRET: 'j'.repeat(64),
      BOT_SYNC_SECRET: 's'.repeat(48),
      DATABASE_URL: '',
    },
    stdio: 'ignore',
  });
  await waitUp();
});

test.after(() => { if (child) child.kill('SIGKILL'); });

test('security headers on static-served HTML and on API responses', async () => {
  for (const p of ['/', '/api/public/track-record']) {
    const r = await req('GET', p);
    const h = r.headers;
    assert.ok(String(h['content-security-policy'] || '').includes("default-src 'self'"), `${p}: CSP`);
    assert.ok(String(h['content-security-policy'] || '').includes('https://telegram.org'), `${p}: CSP allows the Telegram widget`);
    assert.ok(String(h['strict-transport-security'] || '').includes('max-age='), `${p}: HSTS`);
    assert.equal(h['x-content-type-options'], 'nosniff', `${p}: nosniff`);
    assert.equal(h['x-frame-options'], 'DENY', `${p}: frame deny`);
  }
});

test('trust proxy: per-IP limiting buckets by X-Forwarded-For client, not the proxy hop', async () => {
  // Exhaust client A's /mcp budget (60/min)…
  const ping = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping' });
  let lastA = null;
  for (let i = 0; i < 61; i++) {
    lastA = await req('POST', '/mcp', {
      headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.7' },
      body: ping,
    });
  }
  assert.equal(lastA.status, 429, 'client A is rate-limited after 60 calls');
  // …while client B (different forwarded IP, same socket) still gets through.
  const b = await req('POST', '/mcp', {
    headers: { 'Content-Type': 'application/json', 'X-Forwarded-For': '203.0.113.8' },
    body: ping,
  });
  assert.equal(b.status, 200, 'client B has its own bucket');
});

test('MCP body cap: >64 KB is rejected with 413', async () => {
  const big = JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'ping', params: { pad: 'x'.repeat(70000) } });
  const r = await req('POST', '/mcp', {
    headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(big) },
    body: big,
  });
  assert.equal(r.status, 413);
});

test('MCP argument validation: wrong types and unknown keys are -32602, valid calls pass', async () => {
  const call = (args) => req('POST', '/mcp', {
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      jsonrpc: '2.0', id: 2, method: 'tools/call',
      params: { name: 'run_what_if', arguments: args },
    }),
  });
  const bad = await call({ stake_usd: 'a-string' });
  assert.equal(bad.data.error && bad.data.error.code, -32602, 'typed mismatch rejected');
  const unknown = await call({ nonsense_key: 1 });
  assert.equal(unknown.data.error && unknown.data.error.code, -32602, 'unknown key rejected');
  const good = await call({ stake_usd: 1000 });
  assert.equal(good.status, 200);
  assert.ok(good.data.result, 'valid arguments dispatch');
});

test('auth still fronts the per-user surface', async () => {
  for (const p of ['/api/alerts', '/api/networth', '/api/exposure']) {
    const r = await req('GET', p);
    assert.equal(r.status, 401, `${p} anonymous → 401`);
  }
});
