/**
 * Dashboard-shell performance pass: the logged-in app shell (loaded on every
 * visit by every authenticated user) gets the same load-speed treatment as the
 * landing page — the render-blocking wallet_picker script is deferred, and the
 * heavy 3D agent (three.js ~150KB+) loads via a guarded dynamic import that is
 * skipped entirely under prefers-reduced-motion and deferred to idle. The
 * importmap stays in <head> so the idle import can still resolve "three".
 * Verified by source assertion.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('wallet_picker is no longer render-blocking (deferred)', () => {
  assert.match(html, /<script src="\/js\/wallet_picker\.js" defer><\/script>/);
});

test('the 3D agent is not a static eager module script anymore', () => {
  assert.doesNotMatch(html, /<script type="module" src="\/js\/mascot3d\.js/);
});

test('the 3D agent loads via a reduced-motion-guarded, idle-deferred dynamic import', () => {
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /import\('\/js\/mascot3d\.js/);
  assert.match(html, /requestIdleCallback/);
});

test('the three.js importmap is still present so the idle import can resolve "three"', () => {
  assert.match(html, /<script type="importmap">/);
  assert.match(html, /"three":\s*"\/vendor\/three\/three\.module\.min\.js"/);
});
