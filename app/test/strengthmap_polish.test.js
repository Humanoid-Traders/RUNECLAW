'use strict';
/**
 * 3D Strength Map — advanced look + motion. Glow halos, a starfield, a floor
 * grid, smooth morphing when axes/bias/data change, staggered fade-in, hover
 * tooltip, projected axis labels, idle auto-orbit, and animated panel bars /
 * count-ups. All motion is gated on prefers-reduced-motion; the 2D fallback and
 * §4 (public data only) are unchanged.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'strengthmap.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strengthmap.html'), 'utf8');

test('the scene has glow halos, a starfield and a floor grid', () => {
  assert.match(js, /THREE\.Sprite\(/);                 // additive glow halos
  assert.match(js, /AdditiveBlending/);
  assert.match(js, /THREE\.Points\(/);                 // starfield
  assert.match(js, /GridHelper/);                      // floor grid
});

test('points morph via persistent per-symbol nodes (no snap on relayout)', () => {
  assert.match(js, /const nodes = new Map\(\)/);
  assert.match(js, /n\.cur\.x \+= \(n\.tgt\.x - n\.cur\.x\) \* k/);   // lerp toward target
  assert.match(js, /function targetFor/);
});

test('idle auto-orbit yields to interaction and pauses on selection', () => {
  assert.match(js, /controls\.autoRotate = !REDUCED && !state\.sel && \(t - lastInteract > \d+\)/);
});

test('hover shows a floating tooltip; axis labels are projected each frame', () => {
  assert.match(js, /processHover/);
  assert.match(js, /smTip|'smTip'|\$\('smTip'\)|getElementById\('smTip'\)/);
  assert.match(js, /updateAxisLabels/);
  assert.match(js, /\.project\(camera\)/);
  assert.match(html, /id="smTip"/);
  assert.match(html, /\.sm-axlabel/);
});

test('all motion is gated on prefers-reduced-motion', () => {
  assert.match(js, /const REDUCED = /);
  assert.match(js, /matchMedia\('\(prefers-reduced-motion: reduce\)'\)/);
  assert.match(js, /!REDUCED/);                        // used to gate animation
  assert.match(html, /prefers-reduced-motion: reduce/); // css also honours it
});

test('panel scores count up and factor bars grow (CSS transition + JS)', () => {
  assert.match(js, /function countUp/);
  assert.match(js, /el\.style\.width = el\.dataset\.w/);        // bar grow
  assert.match(html, /\.sm-frow \.bar i \{ transition:/);       // the transition that animates it
});

test('§4 + fallback intact: public data only, 2D table still there', () => {
  assert.match(js, /renderFallback/);
  assert.match(js, /\/api\/market\/strengthmap/);
  assert.ok(!/\/api\/portfolio|\/api\/trades|\/api\/trade\b|equity|net_pnl|balance|live_executor|wallet_address/.test(js));
});
