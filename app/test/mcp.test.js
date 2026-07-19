'use strict';
/**
 * RUNECLAW MCP server: protocol handshake, tool listing with read-only
 * annotations, tool calls over real (seeded/faked) data, JSON-RPC error
 * shapes, and the no-stream statelessness contract.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;   // get_proof_of_pnl must fail-closed unconfigured

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const rwa = require('../lib/rwa');
const dex = require('../lib/dex');

let server, base;

test.before(async () => {
  rwa.setTickerFetcher(async () => ({
    BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
    ONDOUSDT: { price: 1, change: 5, volume: 1e7 },
  }));
  dex.setTickerFetcher(async () => ({ BTCUSDT: { price: 100000, change: 1, volume: 1e9 } }));
  dex.setMidsFetcher(async () => ({ BTC: '100050' }));

  // Seed one closed operator trade so history-backed tools have data.
  await pool.execute(
    `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
      size_usd, pnl, fees, status, pattern, opened_at, closed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
    [1, 'BTC/USDT', 'LONG', 100, 110, 1000, 100, 1, null,
     new Date(Date.now() - 86400000), new Date()]);

  const app = express();
  app.use('/mcp', require('../routes/mcp'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function rpc(body, method = 'POST') {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? null : JSON.stringify(body);
    const r = http.request(`${base}/mcp`, {
      method,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : null }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

function toolResult(r) {
  assert.equal(r.status, 200);
  assert.equal(r.data.result.isError, false);
  return JSON.parse(r.data.result.content[0].text);
}

test('initialize handshake + initialized notification', async () => {
  const r = await rpc({ jsonrpc: '2.0', id: 1, method: 'initialize',
    params: { protocolVersion: '2025-03-26', capabilities: {} } });
  assert.equal(r.status, 200);
  assert.equal(r.data.result.serverInfo.name, 'runeclaw');
  assert.ok(r.data.result.capabilities.tools);
  assert.match(r.data.result.instructions, /read-only/i);

  const n = await rpc({ jsonrpc: '2.0', method: 'notifications/initialized' });
  assert.equal(n.status, 202);           // notification accepted, no body
});

test('tools/list: every tool is annotated read-only with a schema', async () => {
  const r = await rpc({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
  const tools = r.data.result.tools;
  assert.ok(tools.length >= 8);
  const names = tools.map(t => t.name);
  for (const want of ['get_track_record', 'get_signals', 'get_rwa_radar',
    'get_dex_compare', 'run_what_if', 'get_weekly_letter', 'get_proof_of_pnl']) {
    assert.ok(names.includes(want), want);
  }
  for (const t of tools) {
    assert.equal(t.annotations.readOnlyHint, true, t.name);
    assert.equal(t.inputSchema.type, 'object', t.name);
  }
});

test('tools/call: track record + what-if from the seeded history', async () => {
  const tr = toolResult(await rpc({ jsonrpc: '2.0', id: 3, method: 'tools/call',
    params: { name: 'get_track_record', arguments: {} } }));
  assert.equal(tr.trades, 1);
  assert.equal(tr.net_pnl_usd, 100);
  assert.equal(tr.win_rate_pct, 100);

  const wi = toolResult(await rpc({ jsonrpc: '2.0', id: 4, method: 'tools/call',
    params: { name: 'run_what_if', arguments: { stake_usd: 500 } } }));
  assert.equal(wi.hypothetical, true);
  assert.equal(wi.trades, 1);
  assert.equal(wi.fixed.net_pnl_usd, 50);   // +10% on $500
});

test('tools/call: radar + dex compare ride the injected fixtures', async () => {
  const radar = toolResult(await rpc({ jsonrpc: '2.0', id: 5, method: 'tools/call',
    params: { name: 'get_rwa_radar', arguments: {} } }));
  assert.equal(radar.read_only, true);
  assert.equal(radar.sector.listed, 1);     // ONDO only

  const cmp = toolResult(await rpc({ jsonrpc: '2.0', id: 6, method: 'tools/call',
    params: { name: 'get_dex_compare', arguments: {} } }));
  assert.equal(cmp.rows[0].base, 'BTC');
  assert.equal(cmp.rows[0].delta_bps, 5);
});

test('tools/call: proof_of_pnl fails closed when the gateway is unconfigured', async () => {
  // The tool relays the sealed statement from the bot gateway. With no
  // WEB_GATEWAY_SECRET it must NOT attempt a call or invent data — it reports
  // published:false honestly. (The passthrough shape is the gateway's, already
  // exercised by the gateway suite.)
  const p = toolResult(await rpc({ jsonrpc: '2.0', id: 9, method: 'tools/call',
    params: { name: 'get_proof_of_pnl', arguments: {} } }));
  assert.equal(p.published, false);
  assert.equal(p.error, 'not_configured');
});

test('tools/call: weekly letter generates and carries the honesty footer', async () => {
  const letter = toolResult(await rpc({ jsonrpc: '2.0', id: 7, method: 'tools/call',
    params: { name: 'get_weekly_letter', arguments: {} } }));
  assert.match(letter.week_key, /^\d{4}-W\d{2}$/);
  assert.match(letter.footer, /nothing hand-written/i);
});

test('errors: unknown tool, unknown method, invalid request, no stream', async () => {
  const badTool = await rpc({ jsonrpc: '2.0', id: 8, method: 'tools/call',
    params: { name: 'place_order', arguments: {} } });
  assert.equal(badTool.data.error.code, -32602);

  const badMethod = await rpc({ jsonrpc: '2.0', id: 9, method: 'resources/list' });
  assert.equal(badMethod.data.error.code, -32601);

  const invalid = await rpc({ hello: 'world' });
  assert.equal(invalid.data.error.code, -32600);

  const get = await rpc(undefined, 'GET');
  assert.equal(get.status, 405);
});
