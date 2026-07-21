/**
 * Mobile-first pass: on touch devices, interactive controls get ≥44px tap
 * targets and inputs render at ≥16px so iOS Safari doesn't zoom on focus.
 * Scoped to `pointer: coarse` so desktop is untouched. Verified by source
 * assertion on the shared stylesheet.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

// Isolate the coarse-pointer block so assertions are about touch rules only.
const m = css.match(/@media \(pointer: coarse\)\s*\{([\s\S]*?)\n\}/);
test('there is a coarse-pointer (touch) refinement block', () => {
  assert.ok(m, 'expected an @media (pointer: coarse) block');
});

test('touch tap targets meet the 44px guideline', () => {
  const block = m[1];
  assert.match(block, /\.btn \{ min-height: 44px/);
  assert.match(block, /\.tab-btn \{ min-height: 44px/);
  assert.match(block, /\.tabbar a \{ min-height: 48px/);
  assert.match(block, /\.nav-links a[^}]*min-height: 44px/);
});

test('inputs are 16px on touch to stop iOS focus-zoom', () => {
  const block = m[1];
  assert.match(block, /\.input[^}]*font-size: 16px/);
});
