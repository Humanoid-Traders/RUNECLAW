'use strict';
/**
 * MH3 — public /status trust surface. Contract: every component state is
 * computed from real timestamps; degraded/no-data states are reported
 * honestly (never rounded up); the payload carries no secrets and no
 * dollar figures.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const status = require('../lib/status');

const NOW = 1_800_000_000_000;

function probes(overrides = {}) {
  return {
    getScan: async () => ({ received_at: new Date(NOW - 5 * 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(NOW - 30 * 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'reachable' }),
    latestLetter: async () => ({ week_key: '2026-W29',
      generated_at: new Date(NOW - 2 * 86_400_000).toISOString() }),
    dbMode: () => 'memory',
    uptimeS: () => 3700,
    ...overrides,
  };
}

test('all-healthy: overall ok, ages computed from real timestamps', async () => {
  status.setProbes(probes());
  const s = await status.buildStatus(NOW);
  assert.equal(s.status, 'ok');
  assert.equal(s.components.engine_scan.state, 'fresh');
  assert.equal(s.components.engine_scan.age_minutes, 5);
  assert.equal(s.components.intelligence_reports.age_minutes, 30);
  assert.equal(s.components.bot_gateway.state, 'reachable');
  assert.equal(s.components.weekly_letter.latest_week, '2026-W29');
});

test('stale scan + dead gateway: degraded, never rounded up to healthy', async () => {
  status.setProbes(probes({
    getScan: async () => ({ received_at: new Date(NOW - 90 * 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'unreachable' }),
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.status, 'degraded');
  assert.equal(s.components.engine_scan.state, 'stale');
  assert.equal(s.components.engine_scan.age_minutes, 90);
});

test('missing data and throwing probes read no_data — not ok, not a crash', async () => {
  status.setProbes(probes({
    getScan: async () => null,
    getReports: async () => { throw new Error('db down'); },
    latestLetter: async () => { throw new Error('db down'); },
  }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.engine_scan.state, 'no_data');
  assert.equal(s.components.intelligence_reports.state, 'no_data');
  assert.equal(s.components.weekly_letter.state, 'no_data');
  assert.equal(s.status, 'degraded');
});

test('not_configured gateway is honest but not alarming', async () => {
  status.setProbes(probes({ pingGateway: async () => ({ state: 'not_configured' }) }));
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.bot_gateway.state, 'not_configured');
  assert.equal(s.status, 'ok');
});

test('payload carries no secrets and no dollar figures', async () => {
  status.setProbes(probes());
  const raw = JSON.stringify(await status.buildStatus(NOW));
  for (const needle of ['$', 'usd', 'secret', 'token', 'password', 'key']) {
    assert.ok(!raw.toLowerCase().includes(needle), `payload must not contain "${needle}"`);
  }
});

test('HTTP surface serves the page and the API', async () => {
  // The route calls buildStatus() with the real clock — feed it live-relative
  // timestamps (the pinned-NOW probes would read as future/no_data here).
  status.setProbes(probes({
    getScan: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(Date.now() - 60_000).toISOString() }),
    latestLetter: async () => ({ week_key: '2026-W29',
      generated_at: new Date(Date.now() - 86_400_000).toISOString() }),
  }));
  const app = express();
  app.use('/api/public/status', require('../routes/public_status'));
  const server = await new Promise((res) => {
    const s = app.listen(0, '127.0.0.1', () => res(s));
  });
  const base = `http://127.0.0.1:${server.address().port}`;
  const body = await new Promise((resolve, reject) => {
    http.get(`${base}/api/public/status`, (r) => {
      let d = '';
      r.on('data', c => d += c);
      r.on('end', () => resolve(JSON.parse(d)));
    }).on('error', reject);
  });
  server.close();
  assert.equal(body.status, 'ok');

  const fs = require('node:fs');
  const path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'status.html'), 'utf8');
  assert.match(html, /never hand-set/, 'the honesty promise is on the page');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/status"/, 'footer link exists');
  const server_js = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_js, /app\.get\('\/status'/, 'page route mounted');
});
