'use strict';
/**
 * Order ticket — decision-picture panel. The engine's live directional read
 * (confluence + top voters) for the typed symbol sits beside the ticket, so the
 * "why" is next to the "buy". Read-only context reusing the same _insightBlock
 * as the market view — never an order path, and it degrades honestly when the
 * analysis bridge is offline.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the trade view renders a decision-picture panel', () => {
  assert.match(dash, /id="p-tinsight"/);
  assert.match(dash, /id="c-tinsight"/);
  assert.match(dash, />Decision picture/);
});

test('it fetches the live insight for the ticket symbol and reuses _insightBlock', () => {
  assert.match(dash, /async function drawTicketInsight/);
  assert.match(dash, /\/api\/insight\?symbol=/);
  assert.match(dash, /_insightBlock\(r && r\.data\)/);
});

test('it refreshes debounced on the symbol input and on deep-link arrival', () => {
  assert.match(dash, /\$\('tSym'\)\.addEventListener\('input', \(\) => \{ clearTimeout\(_tiTimer\); _tiTimer = setTimeout\(drawTicketInsight, 500\); \}\)/);
  // the ?trade= deep-link primes the panel too
  assert.match(dash, /try \{ drawTicketInsight\(\); \} catch \(e\) \{ [^}]*insight is best-effort/);
});

test('the panel is read-only context — no order path lives in it', () => {
  // _insightBlock (the panel body) must not POST or reference a trade/order path.
  const block = dash.slice(dash.indexOf('function _insightBlock'), dash.indexOf('async function openSymbol'));
  assert.ok(!/\/api\/trade|propose|order_type|method:\s*'POST'/.test(block),
    '_insightBlock renders read-only market context, never an order path');
});

test('the dashboard.js cache-buster is bumped so the panel ships', () => {
  assert.match(html, /dashboard\.js\?v=\d\d+/);
});
