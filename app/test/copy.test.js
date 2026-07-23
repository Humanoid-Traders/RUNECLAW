'use strict';
/**
 * MARKETPLACE Phase 3 — follow + paper-copy a strategy-agent.
 *
 * Covers the pure gate-matcher (lib/agent_match), the follow/unfollow/list +
 * picks API (routes/copy), and the §4 guarantees: follow moves no funds, picks
 * are the live signals an agent's OWN gates would take, and copying is the
 * user-initiated paper-trade prefill (data-ptrade) — never auto-execution.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('fs');
const path = require('path');
const express = require('express');

const { agentWouldTake, matchableGates, picksForAgent, baseOf } = require('../lib/agent_match');

// ─── Pure matcher ──────────────────────────────────────────────────────────
test('agentWouldTake enforces only the gates the signal payload exposes', () => {
  const gates = { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN', rsi_max: 35, symbols: null };
  // matches: confidence >= 0.7 AND regime === TREND_DOWN
  assert.ok(agentWouldTake({ confidence: 0.8, regime: 'TREND_DOWN', symbol: 'BTC/USDT' }, gates));
  // fails confidence
  assert.ok(!agentWouldTake({ confidence: 0.6, regime: 'TREND_DOWN', symbol: 'BTC/USDT' }, gates));
  // fails regime
  assert.ok(!agentWouldTake({ confidence: 0.9, regime: 'TREND_UP', symbol: 'BTC/USDT' }, gates));
  // rsi_max is NOT in the payload → never causes a match/miss on its own
  assert.ok(agentWouldTake({ confidence: 0.75, regime: 'TREND_DOWN', symbol: 'ETH/USDT' }, gates));
});

test('symbol allow-list matches on the base ticker across quote/settle suffixes', () => {
  const gates = { confidence_threshold: 0, symbols: ['BTC', 'ETH'] };
  assert.strictEqual(baseOf('BTC/USDT:USDT'), 'BTC');
  assert.ok(agentWouldTake({ confidence: 0.5, symbol: 'BTC/USDT' }, gates));
  assert.ok(agentWouldTake({ confidence: 0.5, symbol: 'ETH/USDT:USDT' }, gates));
  assert.ok(!agentWouldTake({ confidence: 0.5, symbol: 'SOL/USDT' }, gates));
});

test('matchableGates reports only checkable gates (never rsi/volume)', () => {
  const applied = matchableGates({ confidence_threshold: 0.7, regime_filter: 'TREND_UP', rsi_max: 30, volume_spike_min: 2, symbols: ['BTC'] });
  assert.deepStrictEqual(applied.sort(), ['confidence', 'regime', 'symbols']);
  assert.deepStrictEqual(matchableGates({ rsi_max: 30, volume_spike_min: 2 }), []);
});

test('picksForAgent returns the matching subset, capped and labelled', () => {
  const agent = { id: 'dip-sniper', name: 'Dip Sniper', scorecard: { gates: { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN' } } };
  const sigs = [
    { symbol: 'BTC/USDT', confidence: 0.8, regime: 'TREND_DOWN', direction: 'LONG' },
    { symbol: 'ETH/USDT', confidence: 0.5, regime: 'TREND_DOWN', direction: 'LONG' },   // low conf
    { symbol: 'SOL/USDT', confidence: 0.9, regime: 'TREND_UP', direction: 'LONG' },     // wrong regime
  ];
  const out = picksForAgent(agent, sigs);
  assert.strictEqual(out.id, 'dip-sniper');
  assert.strictEqual(out.picks.length, 1);
  assert.strictEqual(out.picks[0].symbol, 'BTC/USDT');
  assert.deepStrictEqual(out.matched_on.sort(), ['confidence', 'regime']);
});

// ─── API (mock DB + JWT, gateway stubbed) ────────────────────────────────────
const CATALOG = [
  { id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯', scorecard: { gates: { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN' } } },
  { id: 'momentum-hunter', name: 'Momentum Hunter', icon: '🚀', scorecard: { gates: { confidence_threshold: 0.6, regime_filter: 'TREND_UP' } } },
];

let server, base, pool;

test.before(async () => {
  // Stub the gateway BEFORE routes/copy captures its destructured imports.
  const gateway = require('../lib/gateway');
  gateway.isConfigured = () => true;
  gateway.getGateway = async () => ({ status: 200, data: { agents: CATALOG } });

  ({ pool } = require('../db'));
  // Seed the live signal stream: one dip match, one momentum match, one miss.
  const ins = (k, sym, dir, conf, regime) => pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score, pattern,
       regime, entry_price, stop_loss, take_profit, rr, thesis, status, pnl,
       created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [k, sym, dir, conf, conf, 'x', regime, 100, 95, 110, 2, '', 'OPEN', null,
     new Date().toISOString(), null]);
  await ins('s1', 'BTC/USDT', 'LONG', 0.82, 'TREND_DOWN');   // dip-sniper
  await ins('s2', 'SOL/USDT', 'LONG', 0.55, 'TREND_DOWN');   // nobody (low conf)
  await ins('s3', 'ETH/USDT', 'LONG', 0.71, 'TREND_UP');     // momentum-hunter

  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/copy', require('../routes/copy'));
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
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

async function tokenFor(email) {
  const reg = await req('POST', '/api/auth/register', { body: { email, password: 'x'.repeat(12) } });
  return reg.data.token;
}

test('follow requires auth', async () => {
  const r = await req('GET', '/api/copy');
  assert.strictEqual(r.status, 401);
});

test('follow → list → unfollow round-trips; invalid ids rejected', async () => {
  const token = await tokenFor('copy1@test.io');
  let r = await req('GET', '/api/copy', { token });
  assert.deepStrictEqual(r.data.following, []);

  r = await req('POST', '/api/copy/follow', { token, body: { agent_id: 'BAD ID!' } });
  assert.strictEqual(r.status, 400);

  r = await req('POST', '/api/copy/follow', { token, body: { agent_id: 'dip-sniper' } });
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.data.following, ['dip-sniper']);

  // Idempotent: following again keeps a single entry.
  r = await req('POST', '/api/copy/follow', { token, body: { agent_id: 'dip-sniper' } });
  assert.deepStrictEqual(r.data.following, ['dip-sniper']);

  r = await req('POST', '/api/copy/unfollow', { token, body: { agent_id: 'dip-sniper' } });
  assert.deepStrictEqual(r.data.following, []);
});

test('picks returns only the live signals each followed agent\'s gates would take', async () => {
  const token = await tokenFor('copy2@test.io');
  await req('POST', '/api/copy/follow', { token, body: { agent_id: 'dip-sniper' } });
  await req('POST', '/api/copy/follow', { token, body: { agent_id: 'momentum-hunter' } });

  const r = await req('GET', '/api/copy/picks', { token });
  assert.strictEqual(r.status, 200);
  const byId = Object.fromEntries(r.data.agents.map(a => [a.id, a]));

  // dip-sniper: only the TREND_DOWN / high-confidence BTC signal.
  assert.deepStrictEqual(byId['dip-sniper'].picks.map(p => p.symbol), ['BTC/USDT']);
  // momentum-hunter: only the TREND_UP ETH signal.
  assert.deepStrictEqual(byId['momentum-hunter'].picks.map(p => p.symbol), ['ETH/USDT']);
  // the low-confidence SOL signal matched nobody.
  const allPicked = r.data.agents.flatMap(a => a.picks.map(p => p.symbol));
  assert.ok(!allPicked.includes('SOL/USDT'));
});

test('picks is empty (not an error) when following nobody', async () => {
  const token = await tokenFor('copy3@test.io');
  const r = await req('GET', '/api/copy/picks', { token });
  assert.strictEqual(r.status, 200);
  assert.deepStrictEqual(r.data.agents, []);
});

// ─── §4 / UI wiring (source-asserted) ────────────────────────────────────────
test('§4: follow/copy never moves funds and copying is user-initiated paper', () => {
  const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'copy.js'), 'utf8');
  // The route places no trades and never calls the live-trade path.
  assert.ok(!/trade\/confirm|live_executor|_can_trade_live/.test(route), 'copy route must not touch execution');
  // The matcher is pure gate logic — no trade placement, no money formatting.
  const lib = fs.readFileSync(path.join(__dirname, '..', 'lib', 'agent_match.js'), 'utf8');
  assert.ok(!/fmtMoney|toLocaleString|confirm|execute\(/.test(lib), 'matcher must be pure — no execution/formatting');

  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // Follow button + picks panel wired; copy reuses the standard paper prefill.
  assert.match(dash, /data-agentfollow=/);
  assert.match(dash, /id="p-agentpicks"/);
  assert.match(dash, /loadAgentPicks\(\)/);
  assert.match(dash, /\/api\/copy\/picks/);
  // the pick's "Paper-trade" button is the existing one-tap paper prefill.
  assert.match(dash, /data-ptrade='\$\{pt\}'/);
});

test('cache-buster bumped so the follow/copy UI ships', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(html, /dashboard\.js\?v=(7[7-9]|8\d)/);
});
