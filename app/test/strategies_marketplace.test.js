'use strict';
/**
 * MARKETPLACE Phase 1 — the public Strategy-Agent catalogue.
 * The relay route is public (no auth), 503s when the bridge is off, passes the
 * gateway payload straight through, and caches so a spike can't hammer the bot.
 * The dashboard carries a "Strategy Agents" view wired into nav + router that
 * renders one card per real agent with honest CTAs (Lab backtest, ask-the-agent)
 * and never a dollar figure (§4).
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

// Stub the gateway BEFORE the route requires it.
const gateway = require('../lib/gateway');
let configured = true;
const calls = [];
gateway.isConfigured = () => configured;
gateway.getGateway = async (p) => {
  calls.push(p);
  return { status: 200, data: {
    read_only: true, public: true,
    agents: [
      { id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯', tagline: 'Buys capitulation.',
        how: 'Trades all scanned pairs, only in trend down, RSI below 35.',
        regime: 'Downtrends', risk: 'balanced', risk_label: '🟡 Balanced',
        horizon: 'swing', run: 'dip' },
    ],
    note: 'Backtest any of them in the Strategy Lab.',
  } };
};

let server, base;

test.before(async () => {
  const app = express();
  app.use('/api/public/strategies', require('../routes/public_strategies'));
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

test('the catalogue relays the gateway payload and is public (no auth)', async () => {
  configured = true;
  const r = await get('/api/public/strategies');
  assert.equal(r.status, 200);
  assert.equal(r.data.public, true);
  assert.ok(Array.isArray(r.data.agents) && r.data.agents.length >= 1);
  assert.equal(r.data.agents[0].id, 'dip-sniper');
  assert.match(calls[calls.length - 1], /\/public\/strategies/);
});

test('a second hit is served from cache (no new gateway call)', async () => {
  const n = calls.length;
  const r = await get('/api/public/strategies');
  assert.equal(r.status, 200);
  assert.equal(calls.length, n, 'catalogue served from its own cache');
});

test('503 when the bridge is not configured', async () => {
  // isConfigured() is checked before the cache, so a warm cache still 503s.
  configured = false;
  const r = await get('/api/public/strategies');
  configured = true;
  assert.equal(r.status, 503);
});

test('the strategies route is mounted in the server', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.use\('\/api\/public\/strategies', require\('\.\/routes\/public_strategies'\)\)/);
});

test('the dashboard has a Strategy Agents view wired into nav + router', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /\{ id: 'agents',\s*label: 'Agents'/);
  assert.match(dash, /agents: renderAgents/);
  assert.match(dash, /async function renderAgents\(\)/);
  // Fetches the public catalogue without auth and renders per-agent cards.
  assert.match(dash, /fetchJSON\('\/api\/public\/strategies',\s*\{[^}]*auth: false/);
  assert.match(dash, /data-agentlab=/);   // "Backtest in Lab" CTA
  assert.match(dash, /data-agentask=/);   // "Ask the agent" CTA
  assert.match(dash, /a\.how/);           // the derived "how it trades" line
});

test('the marketplace view is §4-safe — no dollar figures rendered by the UI', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // Isolate the renderAgents body and assert it never emits a "$" price token.
  const start = dash.indexOf('async function renderAgents()');
  const end = dash.indexOf('async function renderLeaderboard()', start);
  assert.ok(start > 0 && end > start);
  const body = dash.slice(start, end);
  assert.ok(!/\$\{[^}]*\}\s*(?:USDT|\/)/.test(body));
  assert.ok(!/\$[0-9]/.test(body), 'no literal dollar amounts in the marketplace view');
});

test('the strategies gateway route is registered bot-side', () => {
  const gw = fs.readFileSync(
    path.join(__dirname, '..', '..', 'bot', 'web', 'user_gateway.py'), 'utf8');
  assert.match(gw, /add_get\("\/public\/strategies",\s*handle_strategies_public\)/);
});

// ── Phase 2b: verified scorecards + reproduce-in-Lab ──

test('agent cards render a verified scorecard stat block with provenance', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /function scoreBlock\(sc\)/);
  assert.match(dash, /a\.scorecard/);
  assert.match(dash, /total_return_pct/);
  assert.match(dash, /profit_factor/);
  assert.match(dash, /max_drawdown_pct/);
  assert.match(dash, /Frozen backtest ·/);          // provenance line
  assert.match(dash, /low sample/);                  // low-trade-count guard
});

test('the primary CTA becomes "Reproduce in Lab" when a scorecard exists', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /hasSc \? 'Reproduce in Lab' : 'Backtest in Lab'/);
  // The click handler stashes the EXACT scorecard gates for the Lab to re-run.
  assert.match(dash, /_labReproduce = \{/);
  assert.match(dash, /volume_spike_min: sc\.gates\.volume_spike_min/);
  assert.match(dash, /regime_filter: sc\.gates\.regime_filter/);
  assert.match(dash, /rsi_max: sc\.gates\.rsi_max/);
  // The Lab auto-runs the stashed body through the shared submit path.
  assert.match(dash, /if \(_labReproduce\)/);
  assert.match(dash, /submitLabRun\(rep\.body/);
});

test('the Lab route forwards the preset gate params to the bridge', () => {
  const lab = fs.readFileSync(path.join(__dirname, '..', 'routes', 'lab.js'), 'utf8');
  assert.match(lab, /body\.volume_spike_min = parseFloat/);
  assert.match(lab, /body\.regime_filter = String\(b\.regime_filter\)/);
  assert.match(lab, /body\.rsi_max = parseFloat/);
});

test('the Lab bridge validates + clamps the preset gates', () => {
  const lab = fs.readFileSync(
    path.join(__dirname, '..', '..', 'bot', 'api', 'lab.py'), 'utf8');
  assert.match(lab, /volume_spike_min: Optional\[float\] = None/);
  assert.match(lab, /regime_filter: str = ""/);
  assert.match(lab, /rsi_max: Optional\[float\] = None/);
  assert.match(lab, /"--volume-spike-min"/);
  assert.match(lab, /"--regime-filter"/);
  assert.match(lab, /"--rsi-max"/);
  // Regime is validated against an allowlist pattern before reaching the shell.
  assert.match(lab, /re\.fullmatch\(r"\[A-Z_\]\{1,20\}", regime\)/);
});
