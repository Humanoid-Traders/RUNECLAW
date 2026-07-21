/**
 * Landing conversion pass: trust-badge objection-killers, a "verified" chip on
 * the hero proof strip, and a sticky mobile CTA. Static HTML/CSS, so verified by
 * source assertion. Also guards §4: the additions add no dollar figure to the
 * public page.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('the hero has a trust-badge objection-killer row', () => {
  assert.match(html, /<ul class="trust-badges"/);
  assert.match(html, /Non-custodial/);
  assert.match(html, /Withdrawal-disabled/);
  assert.match(html, /Source-available/);
  assert.match(html, /Every fill <a href="\/proof">verifiable<\/a>/);
});

test('the hero proof strip is stamped verified', () => {
  assert.match(html, /chip chip--up[^>]*>✓ verified/);
});

test('a sticky mobile CTA links to the sign-up panel', () => {
  assert.match(html, /class="mobile-cta" href="#auth-panel"/);
});

test('conversion styles are defined and mobile-CTA is desktop-hidden', () => {
  assert.match(css, /\.trust-badges\b/);
  assert.match(css, /\.trust-badge\b/);
  assert.match(css, /\.mobile-cta \{ display: none; \}/);
  assert.match(css, /@media \(max-width: 639px\)[\s\S]*\.mobile-cta/);
});

test('§4: the new hero trust content adds no dollar figure', () => {
  // Extract the hero block and assert no "$" appears in the trust additions.
  const badges = html.slice(html.indexOf('trust-badges'), html.indexOf('</ul>', html.indexOf('trust-badges')));
  assert.ok(!badges.includes('$'), 'trust badges must be percent/ratio only, no $');
});
