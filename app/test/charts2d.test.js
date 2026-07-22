'use strict';
/**
 * EXP-3D r2: the real-data Canvas charts (public/js/charts2d.js) + their
 * dashboard wiring. Browser-only Canvas code, so source-asserted: the module
 * exposes a donut() + underwater() API returning update/destroy, is reduced-
 * motion safe (draws a static final frame, no rAF loop), cleans up its rAF, and
 * the dashboard mounts both from REAL per-user data (holdings + equity curve)
 * and destroys them on view change (no leaked loop). Visualization only.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const charts = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'charts2d.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('RCCharts exposes donut() and underwater(), each returning update + destroy', () => {
  assert.match(charts, /global\.RCCharts\s*=\s*\{[\s\S]*donut[\s\S]*underwater/);
  // Both mount fns return the { update, destroy } contract.
  const returns = charts.match(/return\s*\{\s*[\s\S]*?update:[\s\S]*?destroy:/g) || [];
  assert.ok(returns.length >= 2, 'both charts return update + destroy');
});

test('the charts are reduced-motion safe and self-cleaning', () => {
  assert.match(charts, /prefers-reduced-motion: reduce/);
  // reduced-motion (or no rAF) → draw ONE final static frame, never loop.
  assert.match(charts, /if \(reduced\(\) \|\| !global\.requestAnimationFrame\) \{ draw\(1\); return; \}/);
  // destroy cancels any pending rAF.
  assert.match(charts, /destroy:[\s\S]*cancelAnimationFrame/);
});

test('the charts are pure Canvas 2D — no WebGL / three / module deps', () => {
  assert.match(charts, /getContext\('2d'\)/);
  assert.ok(!/getContext\(['"]webgl/i.test(charts), 'no WebGL context');
  assert.ok(!/\bTHREE\./.test(charts), 'no three.js');
  assert.ok(!/^\s*import\s/m.test(charts) && !/\brequire\(/.test(charts), 'no module deps');
});

test('the underwater chart derives drawdown from a running peak (<= 0)', () => {
  // Peak-relative drawdown is the whole point — assert the computation shape.
  assert.match(charts, /if \(vals\[i\] > peak\) peak = vals\[i\]/);
  assert.match(charts, /\(vals\[i\] - peak\) \/ peak \* 100/);
});

test('dashboard mounts both charts from REAL data and tears them down on nav', () => {
  assert.match(html, /charts2d\.js/);                         // script included
  assert.match(html, /dashboard\.js\?v=5\d/);                 // cache-buster bumped (v49+)
  assert.match(dash, /id="allocCanvas"/);                     // donut canvas present
  assert.match(dash, /id="underwaterCanvas"/);                // underwater canvas present
  assert.match(dash, /window\.RCCharts\.donut\(/);            // donut mounted
  assert.match(dash, /window\.RCCharts\.underwater\(/);       // underwater mounted
  assert.match(dash, /\/api\/holdings/);                      // donut fed by real holdings
  assert.match(dash, /\/api\/trades\/equity-curve/);          // underwater fed by real equity
  // both handles tracked and destroyed on every view change (no leaked rAF).
  assert.match(dash, /_charts\.push\(h\)/);
  assert.match(dash, /_charts\.forEach\(c => \{ try \{ c\.destroy\(\)/);
});

test('the charts are presented as read-only visualization (never move funds)', () => {
  assert.match(dash, /never move them|nothing here trades/);
});
