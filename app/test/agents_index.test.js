'use strict';
/**
 * Public Strategy-Agent DIRECTORY (/agents) — the no-login index that lists
 * every agent, each card linking to its /agents/:slug page. Source-asserted:
 * the bare /agents route precedes the parametised one, the page renders from the
 * public catalogue, it's §4-safe (percent/ratio only — no dollar figure), and
 * the per-agent page links back to it. Read-only, no money-path.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'agents.html'), 'utf8');
const strategy = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

test('bare /agents is routed BEFORE /agents/:slug (else it is captured as a slug)', () => {
  const iIndex = server.indexOf("app.get('/agents',");
  const iSlug = server.indexOf("app.get('/agents/:slug'");
  assert.ok(iIndex > 0, '/agents route present');
  assert.ok(iSlug > 0, '/agents/:slug route present');
  assert.ok(iIndex < iSlug, '/agents must be declared before /agents/:slug');
  assert.match(server, /app\.get\('\/agents',.*agents\.html/);
});

test('the directory renders every agent from the public catalogue, linking to each slug', () => {
  assert.match(html, /fetch\('\/api\/public\/strategies'/);
  assert.match(html, /list\.map\(card\)/);
  assert.match(html, /href="\/agents\/' \+ encodeURIComponent\(a\.id\)/);
});

test('the directory offers search, regime filter, and sort controls', () => {
  assert.match(html, /id="ag-q"/);       // search box
  assert.match(html, /id="ag-reg"/);     // regime filter
  assert.match(html, /id="ag-sort"/);    // sort select
  // wired to a re-render on interaction
  assert.match(html, /addEventListener\('input', render\)/);
  assert.match(html, /addEventListener\('change', render\)/);
  // the four sort modes exist
  ['return', 'win', 'pf', 'name'].forEach(function (k) {
    assert.ok(new RegExp("sorters\\." + k + "\\b|value=\"" + k + "\"").test(html), 'sort mode ' + k);
  });
});

test('numeric sorts sink agents with no verified metric to the bottom (nulls last)', () => {
  // the desc() comparator must place null metrics after real ones
  assert.match(html, /if \(a == null\) return 1; if \(b == null\) return -1;/);
});

test('§4: the directory shows percent/ratio only — no dollar figure', () => {
  assert.ok(!/\$\s*[0-9{]|'\$'|"\$"|fmtMoney|toLocaleString/.test(html),
    'agents directory must not render a dollar figure');
  assert.match(html, /total_return_pct/);
});

test('the directory is read-only and reduced-motion safe (no trade path)', () => {
  assert.ok(!/\/api\/trade|trade\/confirm|live_executor|api_key/.test(html), 'no money-path on the public directory');
  assert.match(html, /prefers-reduced-motion: reduce/);
});

test('the per-agent page links back to the /agents directory', () => {
  assert.match(strategy, /<a href="\/agents">All agents<\/a>/);
});
