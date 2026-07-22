'use strict';
/**
 * GUARDIAN-INCIDENTS route (app/routes/guardian.js → GET /api/guardian/incidents).
 *
 * Returns the safety incident ledger the bot mirrors into the flight cache; when
 * an older bot hasn't synced an `incidents` array, it derives blocks from
 * REJECTED flight records (derived:true). Read-only, no dollars (§4). Also
 * source-asserts the dashboard incidentsCard exists and is wired.
 */

// Env must be set BEFORE requiring the routers (secrets are read at load time).
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = process.env.BOT_SYNC_SECRET || 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('fs');
const path = require('path');

const sync = require('../routes/sync');
const guardian = require('../routes/guardian');
const BOT_SECRET = process.env.BOT_SYNC_SECRET;

function mount() {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', sync);
  app.use('/api/guardian', guardian);
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

test('incidents ledger: synced incidents are returned with kind counts, no dollars', async () => {
  const app = mount();
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    // Bot syncs a flight payload carrying incidents.
    await req(base, 'POST', '/api/bot/sync/flight', {
      records: [], chain: { ok: true },
      incidents: [
        { id: 'a', ts: '2026-07-22T00:00:00Z', kind: 'block', category: 'Prompt-injection firewall', severity: 'high', symbol: 'BTC', detail: 'blocked injection', chain: { sequence: 9 } },
        { id: 'b', ts: '2026-07-22T00:01:00Z', kind: 'recovery', category: 'Escape plan', severity: 'medium', detail: '2 unwind steps', chain: { sequence: 10 } },
        { id: 'c', ts: '2026-07-22T00:02:00Z', kind: 'flag', category: 'Risk sentinel', severity: 'medium', detail: 'crowding', chain: { sequence: 11 } },
      ],
    });
    const r = await req(base, 'GET', '/api/guardian/incidents');
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.data.read_only, true);
    assert.strictEqual(r.data.derived, false);
    assert.strictEqual(r.data.incidents.length, 3);
    assert.deepStrictEqual(r.data.counts, { block: 1, recovery: 1, flag: 1 });
    assert.ok(!JSON.stringify(r.data).includes('$'));
  } finally { server.close(); }
});

test('incidents ledger: falls back to deriving blocks from REJECTED records', async () => {
  const app = mount();
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    // Older bot: no incidents array, but a REJECTED decision record.
    await req(base, 'POST', '/api/bot/sync/flight', {
      records: [
        { decision_id: 'd1', symbol: 'SOL', outcome: 'REJECTED_ON_RECHECK', timestamp: '2026-07-22T00:00:00Z',
          risk: { reason: 'DRAWDOWN: over cap', checks_failed: ['DRAWDOWN'] }, chain: { entry_hash: 'h1', sequence: 2 } },
        { decision_id: 'd2', symbol: 'ETH', outcome: 'EXECUTED_LIVE', timestamp: '2026-07-22T00:01:00Z', chain: { sequence: 3 } },
      ],
      chain: { ok: true },
      // note: incidents intentionally omitted
    });
    const r = await req(base, 'GET', '/api/guardian/incidents');
    assert.strictEqual(r.status, 200);
    assert.strictEqual(r.data.derived, true);
    assert.strictEqual(r.data.incidents.length, 1);            // only the REJECTED one
    assert.strictEqual(r.data.incidents[0].kind, 'block');
    assert.match(r.data.incidents[0].detail, /DRAWDOWN/);
  } finally { server.close(); }
});

test('dashboard exposes an incidentsCard wired into the Guardian view', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /function incidentsCard\(/);
  assert.match(dash, /id="p-incidents"/);
  assert.match(dash, /\/api\/guardian\/incidents/);
  // no dollar formatting in the incident card (safety surface, §4)
  const cardStart = dash.indexOf('function incidentsCard(');
  const cardBody = dash.slice(cardStart, dash.indexOf('function guardianBlock('));
  assert.ok(!/fmtMoney|\$\{/.test(cardBody) || !cardBody.includes('fmtMoney'), 'no dollar formatting in incidentsCard');
});
