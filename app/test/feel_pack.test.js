'use strict';
/**
 * The "feels alive" pack — an engine heartbeat dot in the dashboard topbar
 * that pulses on real SSE events (and honestly dims after silence), plus
 * haptic ticks on the Arena's moments that matter. All guarded: no vibrate
 * API → silent no-op; reduced-motion neutralizes the pulse animation.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const shell = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');

test('the heartbeat dot pulses on every SSE event and dims honestly', () => {
  assert.match(shell, /id="pulseDot"/);
  assert.match(js, /function beat\(\)/);
  // every stream handler beats
  const handlers = (js.match(/beat\(\);/g) || []).length;
  assert.ok(handlers >= 5, `all five stream handlers beat (found ${handlers})`);
  assert.match(js, /last event \$\{s\}s ago/);
  assert.match(js, /'quiet', s > 90/);
  assert.match(css, /\.pulse-dot\.beat/);
  assert.match(css, /prefers-reduced-motion[^}]*\{[^}]*\.pulse-dot/s);
});

test('arena haptics: fill, close and badge unlock tick — guarded', () => {
  assert.match(arena, /function haptic\(/);
  assert.match(arena, /navigator\.vibrate/);
  assert.match(arena, /haptic\(12\)/);              // fill
  assert.match(arena, /haptic\(\[12, 40, 12\]\)/);  // close
  assert.match(arena, /haptic\(\[16, 30, 16\]\)/);  // badge unlock
});

test('cache-busters bumped so the feel ships', () => {
  const jsV = Number((shell.match(/dashboard\.js\?v=(\d+)/) || [])[1]);
  const cssV = Number((shell.match(/styles\.css\?v=(\d+)/) || [])[1]);
  assert.ok(jsV >= 95, `dashboard.js v>=95 (got ${jsV})`);
  assert.ok(cssV >= 20, `styles.css v>=20 (got ${cssV})`);
});
