'use strict';
/**
 * EXP-3D: the live 3D sector radar (public/js/radar3d.js) + its Guardian/markets
 * wiring. Browser-only Canvas code, so source-asserted: the widget exposes a
 * mount() API, is reduced-motion safe (draws a static frame, no rAF), cleans up
 * its animation loop, and the dashboard mounts it from real RWA-radar data and
 * destroys it on view change (no leaked loop). Visualization only — no trading.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const radar = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'radar3d.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('RC3DRadar exposes a mount() returning update + destroy', () => {
  assert.match(radar, /global\.RC3DRadar\s*=\s*\{\s*mount/);
  assert.match(radar, /return\s*\{\s*[\s\S]*update:[\s\S]*destroy:/);
});

test('the radar is reduced-motion safe and self-cleaning', () => {
  assert.match(radar, /prefers-reduced-motion: reduce/);
  // reduced-motion (or no rAF) → draw ONE static frame, never start the loop.
  assert.match(radar, /if \(reduced\(\) \|\| !global\.requestAnimationFrame\) \{ draw\(null\); return; \}/);
  // destroy stops the loop.
  assert.match(radar, /destroy:[\s\S]*cancelAnimationFrame/);
});

test('the radar is pure Canvas 2D — no WebGL / three dependency', () => {
  assert.match(radar, /getContext\('2d'\)/);
  // Check for real USAGE, not comment words: no WebGL context, no THREE.* calls,
  // no ES-module import/require — it must be a self-contained classic script.
  assert.ok(!/getContext\(['"]webgl/i.test(radar), 'no WebGL context');
  assert.ok(!/\bTHREE\./.test(radar), 'no three.js');
  assert.ok(!/^\s*import\s/m.test(radar) && !/\brequire\(/.test(radar), 'no module deps');
});

test('dashboard mounts the radar from real RWA data and tears it down on nav', () => {
  assert.match(html, /radar3d\.js/);                       // script is included
  assert.match(dash, /id="radar3dCanvas"/);                // canvas present
  assert.match(dash, /window\.RC3DRadar\.mount\(/);        // mounted
  assert.match(dash, /\/api\/market\/rwa/);                // fed by real radar data
  // handle destroyed on every view change (its rAF loop must not leak).
  assert.match(dash, /if \(_radar3d\) \{ try \{ _radar3d\.destroy\(\)/);
});

test('the radar is presented as visualization only (never trades)', () => {
  assert.match(dash, /Visualization only — it never trades/);
});
