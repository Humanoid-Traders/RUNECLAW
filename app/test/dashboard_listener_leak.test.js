'use strict';
/**
 * Readiness-audit fix: the dashboard binds delegated handlers to the PERSISTENT
 * #viewContainer (its node is never replaced — only innerHTML is swapped), so
 * binding inside render functions leaked a listener per render. Worst under the
 * SSE soft refresh that re-renders Portfolio on every live event: one trade-note
 * edit then fired N duplicate PATCH /api/trades/:id/notes, one Share/Run/Join
 * fired N times, plus a steady memory leak.
 *
 * Fix: an onView(type, handler) helper binds via a per-render AbortController
 * (renderAbort), which showView aborts+recreates before each render — so the
 * previous render's delegated listeners are discarded before new ones are added.
 * Source-asserted (dashboard.js is browser-only DOM code).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('delegated view handlers go through the onView helper, not raw container binding', () => {
  // Exactly ONE real container.addEventListener remains — inside the helper.
  const rawBinds = (dash.match(/container\.addEventListener\(/g) || []).length;
  assert.strictEqual(rawBinds, 1,
    'all per-render delegated bindings must use onView(); only the helper may call container.addEventListener');
  // The helper exists and binds with the per-render abort signal.
  assert.match(dash, /function onView\(type, handler\)\s*\{[\s\S]*container\.addEventListener\(type, handler,[\s\S]*renderAbort[\s\S]*signal/);
});

test('showView aborts the previous render listeners and recreates the controller before rendering', () => {
  // The abort+recreate must happen before RENDER[id]() runs.
  const m = dash.match(/if \(renderAbort\) renderAbort\.abort\(\);\s*renderAbort = new AbortController\(\);\s*RENDER\[id\]\(\)/);
  assert.ok(m, 'showView must abort+recreate renderAbort immediately before RENDER[id]()');
});

test('the write-bearing views were actually migrated (spot check)', () => {
  // Portfolio note edit, credential connect, live controls apply, lab run,
  // leaderboard join — all must now route through onView, none through raw bind.
  const onViewCalls = (dash.match(/\bonView\(/g) || []).length;
  // 12 migrated render sites + the helper's own name references in comments.
  assert.ok(onViewCalls >= 12, `expected >=12 onView() usages, found ${onViewCalls}`);
});
