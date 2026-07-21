/**
 * Landing performance pass: the render-blocking wallet_picker script is now
 * deferred, and the heavy 3D hero (three.js ~150KB+) loads via a guarded
 * dynamic import — skipped entirely under prefers-reduced-motion and deferred
 * to idle so it never competes with first paint. Verified by source assertion.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('wallet_picker is no longer render-blocking (deferred)', () => {
  assert.match(html, /<script src="\/js\/wallet_picker\.js" defer><\/script>/);
});

test('the 3D hero is not a static eager module script anymore', () => {
  assert.doesNotMatch(html, /<script type="module" src="\/js\/mascot3d\.js/);
});

test('the 3D hero loads via a reduced-motion-guarded, idle-deferred dynamic import', () => {
  assert.match(html, /prefers-reduced-motion: reduce/);
  assert.match(html, /import\('\/js\/mascot3d\.js/);
  assert.match(html, /requestIdleCallback/);
});
