/**
 * Mission-control home upgrade (MC): a one-glance command bar + the home
 * open-positions panel showing stop-loss protection truth, both reusing the
 * shared slPositionsHtml renderer. DOM code with no headless harness → verified
 * by source assertion (same approach as the other dashboard tests).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('a shared slPositionsHtml renderer exists and both views use it', () => {
  assert.match(dash, /function slPositionsHtml\(d, opts\)/);
  // Home open-positions panel and Portfolio panel both call it.
  const uses = dash.match(/slPositionsHtml\(/g) || [];
  assert.ok(uses.length >= 3, `expected definition + 2 call sites, saw ${uses.length}`);
});

test('the command bar is mounted on Home and reads live safety sources', () => {
  assert.match(dash, /id="p-cmd"/);
  assert.match(dash, /renderPanel\(C\('cmd'\)/);
  assert.match(dash, /fetchJSON\('\/api\/positions'/);   // unprotected count source
  assert.match(dash, /unprotected_count/);
  assert.match(dash, /mc-chip--alert/);                   // red chip when unprotected > 0
});

test('the home positions panel shows protection status (not the raw table)', () => {
  // The hpos panel now renders via slPositionsHtml with a row cap.
  assert.match(dash, /slPositionsHtml\(d, \{ limit: 5 \}\)/);
});

test('command-bar styles are defined and theme-token based', () => {
  assert.match(css, /\.mc-bar\b/);
  assert.match(css, /\.mc-chip\b/);
  assert.match(css, /\.mc-chip--alert\b/);
});
