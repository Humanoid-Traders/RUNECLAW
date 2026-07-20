'use strict';
/**
 * ERC-8257 tool surface — the contract under test:
 * - The manifest is FREE and OPEN (no pricing block, no access predicate) and
 *   generated from the same read-only MCP tool registry as /mcp (no drift).
 * - manifestHash = keccak256(RFC 8785 canonical JSON) — recomputed
 *   independently here.
 * - The registration plan is a DRY RUN: calldata for the operator's own
 *   wallet; the module contains no signing/broadcast primitive (source grep).
 * - The invoke endpoint reaches ONLY whitelisted read-only tools.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://runeclaw.test';
process.env.TOOL_CREATOR_ADDRESS = '0x' + 'ab'.repeat(20);

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');
const t8257 = require('../lib/tool8257');

// ── canonicalization + hash ──────────────────────────────────────────────────

test('jcs: recursively sorted keys, no whitespace, deterministic', () => {
  const a = t8257.jcs({ b: 1, a: { z: [1, 'x', true, null], y: 2 } });
  assert.equal(a, '{"a":{"y":2,"z":[1,"x",true,null]},"b":1}');
  assert.equal(a, t8257.jcs({ a: { y: 2, z: [1, 'x', true, null] }, b: 1 }));
  assert.throws(() => t8257.jcs({ x: Infinity }), /non-finite/);
});

test('manifestHash is keccak256 over the canonical bytes', () => {
  const m = { name: 'x', tags: ['a'] };
  assert.equal(t8257.manifestHash(m),
    ethers.keccak256(ethers.toUtf8Bytes('{"name":"x","tags":["a"]}')));
});

// ── manifest posture ─────────────────────────────────────────────────────────

test('manifest: free, open, read-only, drift-proof against the MCP registry', () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const m = t8257.buildManifest({ tools: TOOLS });
  assert.equal(m.type, 'https://ercs.ethereum.org/ERCS/erc-8257#tool-manifest-v1');
  assert.equal(m.name, 'runeclaw-intel');
  assert.equal(m.endpoint, 'https://runeclaw.test/api/tool/invoke');
  assert.equal(m.creatorAddress, '0x' + 'ab'.repeat(20));
  assert.ok(!('pricing' in m), 'NO pricing block — x402 stays behind the INTEROP §4 gates');
  assert.ok(!('access' in m), 'NO access predicate — open');
  assert.deepEqual(m.inputs.properties.tool.enum, Object.keys(TOOLS),
    'the advertised tool set IS the MCP registry — one source of truth');
  assert.equal(m.verifiability.tier, 'self-attested');
});

test('registration plan: dry-run calldata for the canonical registry, zero-address predicate', () => {
  const TOOLS = require('../routes/mcp').TOOLS;
  const plan = t8257.buildRegistrationPlan({ tools: TOOLS });
  assert.equal(plan.dry_run, true);
  assert.equal(plan.ready, true);
  assert.equal(plan.registry, '0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1');
  assert.equal(plan.recommended_chain_id, 8453, 'Base — same chain as the ERC-8004 root anchor');
  assert.equal(plan.access_predicate, t8257.ZERO_ADDRESS);
  assert.equal(plan.metadata_uri,
    'https://runeclaw.test/.well-known/ai-tool/runeclaw-intel.json');
  // Independently decode the calldata and check it round-trips.
  const iface = new ethers.Interface(
    ['function registerTool(string,bytes32,address) returns (uint256)']);
  const [uri, hash, predicate] = iface.decodeFunctionData('registerTool', plan.calldata);
  assert.equal(uri, plan.metadata_uri);
  assert.equal(hash, t8257.manifestHash(t8257.buildManifest({ tools: TOOLS })));
  assert.equal(predicate.toLowerCase(), t8257.ZERO_ADDRESS);
});

test('non-custodial pin: no signing/broadcast primitive in the module', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'tool8257.js'), 'utf8')
    + fs.readFileSync(path.join(__dirname, '..', 'routes', 'tool8257.js'), 'utf8');
  for (const forbidden of ['sendTransaction', 'signTransaction', 'Wallet(',
    'PRIVATE_KEY', 'signer', 'broadcastTransaction']) {
    assert.ok(!src.includes(forbidden), `must never contain ${forbidden}`);
  }
});

// ── HTTP surface ─────────────────────────────────────────────────────────────

let server, base;

function req(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use(require('../routes/tool8257'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('well-known route serves the manifest; unknown slugs 404', async () => {
  const r = await req('GET', '/.well-known/ai-tool/runeclaw-intel.json');
  assert.equal(r.status, 200);
  assert.equal(r.data.name, 'runeclaw-intel');
  assert.ok(!('pricing' in r.data));
  const miss = await req('GET', '/.well-known/ai-tool/other.json');
  assert.equal(miss.status, 404);
});

test('invoke: whitelisted read-only tool answers; junk is rejected', async () => {
  require('../lib/rwa').setTickerFetcher(async () => ({
    ONDO: null, BTCUSDT: { price: 100000, change: 1, volume: 1e9 },
    ONDOUSDT: { price: 1, change: 2, volume: 1e7 },
  }));
  const ok = await req('POST', '/api/tool/invoke', { tool: 'get_rwa_radar', args: {} });
  assert.equal(ok.status, 200);
  assert.equal(ok.data.tool, 'get_rwa_radar');
  assert.ok(ok.data.result);
  require('../lib/rwa').setTickerFetcher(null);

  const unknown = await req('POST', '/api/tool/invoke', { tool: 'place_order', args: {} });
  assert.equal(unknown.status, 400, 'no order machinery, no unknown tools');
  const badArgs = await req('POST', '/api/tool/invoke',
    { tool: 'get_agent_card', args: { nope: 1 } });
  assert.equal(badArgs.status, 400, 'args validated against the tool schema');
});

test('registration plan endpoint is public dry-run data', async () => {
  const r = await req('GET', '/api/tool/registration-plan');
  assert.equal(r.status, 200);
  assert.equal(r.data.dry_run, true);
  assert.match(r.data.non_custodial_note, /never holds a key/);
});
