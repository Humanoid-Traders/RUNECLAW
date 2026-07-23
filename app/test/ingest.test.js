'use strict';
/**
 * NEWS-3: personal ingest ("share with your agent") — route + UI contract.
 *
 * Source-asserted: the proxy is JWT-authed, resolves identity server-side (the
 * browser can never touch another user's notes), and the News view carries the
 * share panel with its privacy / user-responsibility copy.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('the ingest route is mounted', () => {
  assert.match(read('server.js'), /app\.use\('\/api\/ingest', require\('\.\/routes\/ingest'\)\)/);
});

test('the ingest route is JWT-authed and resolves identity server-side', () => {
  const src = read('routes/ingest.js');
  assert.match(src, /authMiddleware/);
  assert.match(src, /resolveBotIdentity\(req\)/);
  // the browser must never choose whose notes it reads or writes
  assert.ok(!src.includes('req.body.telegram_id') && !src.includes('req.query.telegram_id'),
    'identity comes from the session, not the request');
  assert.match(src, /isConfigured\(\)/);   // 503 when the bridge is off
});

test('the ingest proxy round-trips save / list / delete to the gateway', () => {
  const src = read('routes/ingest.js');
  assert.match(src, /getGateway\(`\/ingest\?telegram_id=/);
  assert.match(src, /postGateway\('\/ingest'/);
  assert.match(src, /postGateway\('\/ingest\/delete'/);
});

test('the News view has a share-with-your-agent panel that is private-only', () => {
  const dash = read('public/js/dashboard.js');
  assert.match(dash, /id="p-newsshare"/);
  assert.match(dash, /function drawShare\(/);
  assert.match(dash, /fetchJSON\('\/api\/ingest'/);
  assert.match(dash, /fetchJSON\('\/api\/ingest',\s*\{\s*method:\s*'POST'/);
  assert.match(dash, /fetchJSON\('\/api\/ingest\/delete'/);
  // compliance copy: private, never shared/public, user-responsibility
  assert.match(dash, /Private to you/);
  assert.match(dash, /never shared or made public/);
  assert.match(dash, /paywalled content/);
});
