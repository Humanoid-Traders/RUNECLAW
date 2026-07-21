/**
 * UX-7: onboarding completion (audit-confirmed). Two dashboard wins verified by
 * source assertion — dashboard.js is a DOM surface with no headless harness.
 *
 * 1. Checklist CTAs used to dump users at the TOP of the long Account view. They
 *    now deep-link to the right panel (#account/akeys etc.), and the router
 *    scrolls + flashes that section into view.
 * 2. Checklist steps flipped to Done silently. A step that just completed (vs a
 *    localStorage snapshot) now gets one celebratory pop.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('the router parses "#view/section" deep links', () => {
  assert.match(dash, /const _slash = String\(id\)\.indexOf\('\/'\)/);
  assert.match(dash, /const section = _slash >= 0 \? id\.slice\(_slash \+ 1\)/);
  // The view part is still validated against VIEWS after stripping the section.
  assert.match(dash, /if \(_slash >= 0\) id = id\.slice\(0, _slash\)/);
});

test('a deep-linked section is scrolled into view and flashed after render', () => {
  assert.match(dash, /document\.getElementById\('p-' \+ section\)/);
  assert.match(dash, /el\.scrollIntoView\(\{ behavior: 'smooth', block: 'start' \}\)/);
  assert.match(dash, /el\.classList\.add\('sec-flash'\)/);
  // Polls for the async-mounted panel rather than assuming it's present.
  assert.match(dash, /_tries\+\+ < 25/);
  assert.match(css, /\.panel\.sec-flash \{ animation: sec-flash/);
});

test('checklist CTAs deep-link to the exact Account panel', () => {
  assert.match(dash, /href: '#account\/aprof'/);   // verify email → profile
  assert.match(dash, /href: '#account\/akeys'/);   // connect exchange → keys
  assert.match(dash, /href: '#account\/atg'/);     // link telegram → tg panel
  assert.match(dash, /href: '#account\/actl'/);    // go live → live controls
});

test('a step that just completed gets a one-time pop, diffed against localStorage', () => {
  assert.match(dash, /localStorage\.getItem\('rc_chk_done'\)/);
  assert.match(dash, /s\._justDone = s\.done && !prevDone\.has\(s\.label\)/);
  assert.match(dash, /localStorage\.setItem\('rc_chk_done'/);
  // The row template carries the pop class only for a freshly-done step.
  assert.match(dash, /\$\{s\._justDone \? ' chk-pop' : ''\}/);
  assert.match(css, /\.chk-item\.chk-pop \{ animation: chk-pop/);
});

test('both onboarding animations respect reduced-motion', () => {
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.chk-item\.chk-pop, \.panel\.sec-flash \{ animation: none/);
});
