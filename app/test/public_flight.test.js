'use strict';
/**
 * PUBLIC Agent Flight Recorder (app/routes/public_flight.js). Serves the sealed
 * decision ledger with NO auth, §4-safe: every dollar figure stripped
 * (sanitizeRecord), percent/ratio/R-multiple kept, chain-integrity window
 * re-derivable. Also covers the shared lib/flight.js sanitizer + the /flight
 * page wiring.
 */
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('fs');
const path = require('path');

const sync = require('../routes/sync');
const publicFlight = require('../routes/public_flight');
const { sanitizeRecord, inspectWindow } = require('../lib/flight');
const BOT_SECRET = process.env.BOT_SYNC_SECRET;

function mount() {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', sync);
  app.use('/api/public/flight', publicFlight);
  return app;
}
function req(base, method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(payload) } : {}),
        'x-bot-secret': BOT_SECRET,
      },
    }, (res) => {
      let d = ''; res.on('data', (c) => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

const RICH = {
  symbol: 'BTC/USDT', decision_id: 'dec-1', timestamp: '2026-07-22T00:00:00Z',
  idea: {
    direction: 'LONG', confidence: 0.72, entry: 65000, sl: 63700, tp: 68000, rr: 2.3, timeframe: '4h',
    votes: [{ name: 'vwap_reclaim', direction: 'bullish', contribution: 0.34 }],
    provenance: { model_provider: 'anthropic', prompt_hash: 'ab'.repeat(16), analysis_version: 'v3', data_bars: 200 },
    explain: { top_bullish: ['reclaimed VWAP'], top_bearish: [] },
  },
  risk: { verdict: 'APPROVED', passed: 9, failed: 0, checks_failed: [], size_usd: 250.75, reason: 'ok' },
  result: { pnl_usd: 42.5, pnl_pct: 1.9, r_multiple: 1.4, close_reason: 'tp' },
  explanation: { narrative: 'Long BTC on the VWAP reclaim; closed for +$42.50 (about +1.9%).' },
  chain: { sequence: 7, entry_hash: 'c'.repeat(64) },
};

test('lib/flight sanitizeRecord strips every dollar field, keeps percent/ratio/prices', () => {
  const s = sanitizeRecord(RICH);
  const j = JSON.stringify(s);
  assert.ok(!/size_usd|pnl_usd/.test(j), 'no dollar-named keys');
  assert.ok(!/\$\s?\d/.test(j), 'no dollar amount anywhere (incl. narrative text)');
  assert.equal(s.risk.verdict, 'APPROVED');        // verdict + checks kept
  assert.equal(s.risk.passed, 9);
  assert.equal(s.idea.entry, 65000);               // prices are public market data — kept
  assert.equal(s.result.pnl_pct, 1.9);             // percent kept
  assert.equal(s.result.r_multiple, 1.4);          // R-multiple kept
  assert.equal(s.idea.provenance.model_provider, 'anthropic');
  assert.match(s.explanation.narrative, /VWAP reclaim/); // narrative kept, $ scrubbed
});

test('GET /api/public/flight returns sanitized records + a re-derivable window', async () => {
  const app = mount();
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await req(base, 'POST', '/api/bot/sync/flight', { records: [RICH], chain: { ok: true, length: 7, tip_hash: 'd'.repeat(64) } });
    const r = await req(base, 'GET', '/api/public/flight');
    assert.equal(r.status, 200);
    assert.equal(r.data.records.length, 1);
    // §4: not a single dollar figure survives to the public surface.
    assert.ok(!JSON.stringify(r.data).includes('$'), 'no dollar amounts in the public payload');
    assert.ok(!/size_usd|pnl_usd/.test(JSON.stringify(r.data)));
    // But the decision chain is intact.
    const rec = r.data.records[0];
    assert.equal(rec.risk.verdict, 'APPROVED');
    assert.equal(rec.idea.direction, 'LONG');
    assert.equal(rec.result.pnl_pct, 1.9);
    assert.ok(r.data.window && r.data.window.well_formed_hashes === 1);
    assert.equal(r.data.chain.length, 7);
  } finally { server.close(); }
});

test('GET /api/public/flight/:decisionId returns one sanitized record (permalink)', async () => {
  const app = mount();
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    await req(base, 'POST', '/api/bot/sync/flight', { records: [RICH], chain: { ok: true } });
    const r = await req(base, 'GET', '/api/public/flight/dec-1');
    assert.equal(r.status, 200);
    assert.equal(r.data.record.decision_id, 'dec-1');
    assert.ok(!JSON.stringify(r.data).includes('$'));
    const miss = await req(base, 'GET', '/api/public/flight/nope');
    assert.equal(miss.status, 404);
  } finally { server.close(); }
});

test('the /flight page + route + nav are wired', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'flight.html'), 'utf8');
  assert.match(html, /\/api\/public\/flight/);       // the page reads the public API
  assert.match(html, /Agent Flight Recorder/);
  assert.match(html, /no dollar amounts/i);           // §4 disclosure on the page
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.get\('\/flight'/);
  assert.match(server, /\/api\/public\/flight.*require\('\.\/routes\/public_flight'\)/);
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/flight"/);
  assert.match(index, /i18n\.js\?v=\d+/);
});

test('guardian.js still shares the same inspectWindow (single source of truth)', () => {
  const g = fs.readFileSync(path.join(__dirname, '..', 'routes', 'guardian.js'), 'utf8');
  assert.match(g, /require\('\.\.\/lib\/flight'\)/);
  // sanity: the shared helper actually flags a broken chain
  const bad = inspectWindow([
    { chain: { sequence: 2, entry_hash: 'x' } },   // malformed hash
    { chain: { sequence: 2, entry_hash: 'a'.repeat(64) } },
  ]);
  assert.ok(bad.problems.length >= 1);
});
