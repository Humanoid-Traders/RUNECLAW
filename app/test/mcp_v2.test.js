'use strict';
/**
 * MCP v2 (PR GG) — the agent-facing tool surface grows to cover everything
 * shipped since v1: research dossiers with the safety read, token safety
 * scans, the verifiable leaderboard + seasons, ERC-8004 agent cards, the
 * percent-only public letter, the guided-only airdrop radar, and alpha
 * intel. Plus /developers, a page that documents the server by asking it.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.BOT_GATEWAY_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const express = require('express');

let server, base;

function rpc(msg) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(msg);
    const r = http.request(`${base}/mcp`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, body: d ? JSON.parse(d) : null }));
    });
    r.on('error', reject);
    r.write(payload);
    r.end();
  });
}

async function callTool(name, args) {
  const r = await rpc({ jsonrpc: '2.0', id: 9, method: 'tools/call',
    params: { name, arguments: args || {} } });
  assert.equal(r.status, 200);
  const c = r.body.result.content[0];
  return { isError: r.body.result.isError, data: JSON.parse(c.text) };
}

test.before(async () => {
  // Deterministic data sources — no network.
  require('../lib/research').setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 5e9 },
  }));
  require('../lib/rwa').setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 5e9 },
  }));
  require('../lib/dex').setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 5e9 },
  }));
  require('../lib/dex').setMidsFetcher(async () => ({}));
  require('../lib/tickers').setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 5e9 },
  }));
  require('../lib/token_safety').setPairSearcher(async () => null);

  const app = express();
  app.use(express.json({ limit: '1mb' }));
  app.use('/mcp', require('../routes/mcp'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('server identifies as v2 and lists every v2 tool read-only', async () => {
  const init = await rpc({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} });
  assert.equal(init.body.result.serverInfo.version, '2.0.0');

  const r = await rpc({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
  const tools = r.body.result.tools;
  const names = tools.map(t => t.name);
  for (const t of ['research_token', 'scan_token_safety', 'get_leaderboard',
    'get_agent_card', 'get_public_letter', 'get_airdrop_radar', 'get_alpha_intel']) {
    assert.ok(names.includes(t), `v2 tool ${t} listed`);
  }
  assert.ok(tools.every(t => t.annotations.readOnlyHint === true), 'all read-only');
});

test('research_token: dossier with safety read for listed, honest refusal for unlisted', async () => {
  const btc = await callTool('research_token', { symbol: 'btc' });
  assert.equal(btc.data.listed, true);
  assert.ok(btc.data.sections.some(s => s.title === 'Safety read'));
  assert.ok(btc.data.safety, 'structured safety payload rides along');

  const nope = await callTool('research_token', { symbol: 'NOPE99' });
  assert.equal(nope.data.listed, false);

  const missing = await rpc({ jsonrpc: '2.0', id: 3, method: 'tools/call',
    params: { name: 'research_token', arguments: {} } });
  assert.match(missing.body.error.message, /Missing required argument: symbol/);
});

test('scan_token_safety carries the never-a-verdict disclaimer', async () => {
  const r = await callTool('scan_token_safety', { symbol: 'BTC' });
  assert.match(r.data.disclaimer, /never a verdict/i);
  assert.ok(['standard', 'elevated', 'high', 'extreme'].includes(r.data.tier));
});

test('get_agent_card validates the address before any lookup', async () => {
  const bad = await callTool('get_agent_card', { address: 'not-an-address' });
  assert.match(bad.data.error, /0x \+ 40 hex/);
  // Well-formed but gateway unconfigured -> honest not_configured, never a fake card.
  const ok = await callTool('get_agent_card', { address: '0x' + 'ab'.repeat(20) });
  assert.equal(ok.data.found, false);
});

test('get_leaderboard: season validated; unconfigured gateway is honest', async () => {
  const bad = await callTool('get_leaderboard', { season: '2026-7' });
  assert.match(bad.data.error, /YYYY-MM/);
  const r = await callTool('get_leaderboard', {});
  assert.equal(r.data.available, false);
});

test('get_public_letter validates the week key shape', async () => {
  const bad = await callTool('get_public_letter', { week: '2026-07' });
  assert.match(bad.data.error, /YYYY-Wnn/);
  const r = await callTool('get_public_letter', {});
  // Empty test DB -> a letter still composes (flat week) and stays dollar-free.
  if (r.data.sections) {
    for (const s of r.data.sections) {
      assert.ok(!String(s.html).includes('$'), `no dollars in public letter via MCP ("${s.title}")`);
    }
  }
});

test('get_airdrop_radar keeps the guided-only + anti-sybil stance on the wire', async () => {
  const r = await callTool('get_airdrop_radar', {});
  assert.match(r.data.anti_sybil, /One human, one wallet/);
  assert.match(r.data.participation, /sign every step/i);
  assert.ok(r.data.campaigns.length >= 3);
});

test('get_alpha_intel returns the derived-only analytics shape', async () => {
  const r = await callTool('get_alpha_intel', {});
  assert.ok('trades' in r.data && 'alpha' in r.data && 'max_drawdown_usd' in r.data);
});

// ── /developers page ─────────────────────────────────────────────────────────

test('the developers page documents the server by asking it (no drift possible)', () => {
  const html = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'developers.html'), 'utf8');
  assert.match(html, /tools\/list/, 'tool catalog rendered from the live registry');
  assert.match(html, /POST \/mcp|'\/mcp'/, 'MCP endpoint documented');
  assert.match(html, /read-only by design/i);
  assert.match(html, /verify the fills/i);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /'\/developers'/, 'page routed');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/developers"/, 'discoverable from the landing footer');
});
