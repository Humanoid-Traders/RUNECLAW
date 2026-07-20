'use strict';
/**
 * Season pass-through on the public leaderboard relay (community C5).
 * The season param is validated hard (junk can't poison the cache or reach
 * the gateway), each season caches under its own key, and the page carries
 * the season tabs wiring.
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

// Stub the gateway BEFORE the route requires it; record requested paths.
const gateway = require('../lib/gateway');
const calls = [];
gateway.getGateway = async (p) => {
  calls.push(p);
  const season = /season=([\d-]+)/.exec(p);
  return { status: 200, data: { rows: [], count: 0, seasons: ['2026-07'],
    ...(season ? { season: season[1] } : {}) } };
};

let server, base;

test.before(async () => {
  const app = express();
  app.use('/api/public/leaderboard', require('../routes/public_leaderboard'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function get(p) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${p}`, { method: 'GET' }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d || '{}') }));
    });
    r.on('error', reject);
    r.end();
  });
}

test('season param passes through validated; junk is stripped; cache is per-key', async () => {
  let r = await get('/api/public/leaderboard?season=2026-07');
  assert.equal(r.status, 200);
  assert.equal(r.data.season, '2026-07');
  assert.match(calls[calls.length - 1], /season=2026-07/);

  r = await get('/api/public/leaderboard');                      // live board
  assert.equal(r.status, 200);
  assert.ok(!r.data.season);
  assert.ok(!/season=/.test(calls[calls.length - 1]));

  // Junk season → treated as live, never forwarded.
  const before = calls.length;
  r = await get('/api/public/leaderboard?season=DROP%20TABLE');
  assert.equal(r.status, 200);
  assert.ok(!r.data.season);
  // Either served from the live cache (no new call) or fetched without season.
  if (calls.length > before) assert.ok(!/season=/.test(calls[calls.length - 1]));

  // Cached: repeating the season fetch adds no gateway call.
  const n = calls.length;
  await get('/api/public/leaderboard?season=2026-07');
  assert.equal(calls.length, n, 'season response served from its own cache key');
});

test('leaderboard page carries the season tabs wiring', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'leaderboard.html'), 'utf8');
  assert.match(html, /id="seasons"/);
  assert.match(html, /season-tab/);
  assert.match(html, /\?season=/);
  assert.match(html, /data-season/);
});
