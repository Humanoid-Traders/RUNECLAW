/**
 * dApp connectors hub.
 *
 * Part 1 — the curated catalog (app/lib/dapps.js): filtering by category/chain,
 * catalog integrity (every entry well-formed with an https url and a known
 * category), and that it stays a directory (no execution surface).
 * Part 2 — the public GET /api/dapps route shape.
 *
 * Run: npm test  (node --test test/)
 */

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const fs = require('fs');
const path = require('path');

const dapps = require('../lib/dapps');

// ── Part 1: catalog ──────────────────────────────────────────────────────

test('every dApp entry is well-formed', () => {
  assert.ok(dapps.DAPPS.length >= 12);
  const ids = new Set();
  for (const d of dapps.DAPPS) {
    assert.ok(d.id && !ids.has(d.id), `unique id: ${d.id}`); ids.add(d.id);
    assert.ok(d.name && d.blurb && d.emoji);
    assert.ok(dapps.CATEGORIES.includes(d.category), `known category: ${d.category}`);
    assert.match(d.url, /^https:\/\//, `https url: ${d.id}`);
    assert.ok(Array.isArray(d.chains) && d.chains.length > 0);
  }
});

test('listDapps filters by category and chain (case-insensitive)', () => {
  const dex = dapps.listDapps({ category: 'DEX' });
  assert.ok(dex.length > 0 && dex.every(d => d.category === 'DEX'));
  const base = dapps.listDapps({ chain: 'BASE' });
  assert.ok(base.length > 0 && base.every(d => d.chains.includes('base')));
  const both = dapps.listDapps({ category: 'Lending', chain: 'base' });
  assert.ok(both.every(d => d.category === 'Lending' && d.chains.includes('base')));
});

test('listDapps returns [] for an unknown filter, never throws', () => {
  assert.deepStrictEqual(dapps.listDapps({ category: 'nope' }), []);
  assert.deepStrictEqual(dapps.listDapps({ chain: 'nope' }), []);
});

test('chains() enumerates the distinct chains present with labels', () => {
  const cs = dapps.chains();
  assert.ok(cs.some(c => c.key === 'ethereum' && c.label === 'Ethereum'));
  const keys = cs.map(c => c.key);
  assert.strictEqual(new Set(keys).size, keys.length); // distinct
});

test('the catalog is a directory, not an execution surface (§4)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'dapps.js'), 'utf8');
  // No signing / routing / execution machinery lives in the catalog.
  assert.ok(!/eth_sendTransaction|signTransaction|writeContract|sendTransaction\(/.test(src));
  assert.match(dapps.NOTE, /never routes or executes/i);
});

// ── Part 2: route ────────────────────────────────────────────────────────

test('GET /api/dapps returns the directory + facets', async () => {
  const express = require('express');
  const app = express();
  app.use('/api/dapps', require('../routes/dapps'));
  const server = await new Promise((resolve) => { const s = app.listen(0, '127.0.0.1', () => resolve(s)); });
  const base = `http://127.0.0.1:${server.address().port}`;
  const body = await new Promise((resolve, reject) => {
    http.get(`${base}/api/dapps?category=DEX`, (res) => {
      let d = ''; res.on('data', c => d += c); res.on('end', () => resolve({ status: res.statusCode, json: JSON.parse(d) }));
    }).on('error', reject);
  });
  server.close();
  assert.strictEqual(body.status, 200);
  assert.strictEqual(body.json.read_only, true);
  assert.ok(body.json.dapps.every(d => d.category === 'DEX'));
  assert.ok(Array.isArray(body.json.categories) && body.json.categories.includes('DEX'));
  assert.ok(Array.isArray(body.json.chains));
  assert.match(body.json.note, /verify the URL/i);
});
