'use strict';
/**
 * Quality-hardening — accessibility + mobile on the Guardian / tool pages.
 * Every standalone tool page gets a keyboard-focus ring and a main landmark;
 * the position builders stay tappable on phones.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const PAGES = ['guardian', 'flight', 'stress', 'sentinel', 'firewall', 'escape', 'intent'];
const read = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f + '.html'), 'utf8');

test('every tool page has a visible keyboard-focus ring', () => {
  for (const p of PAGES) {
    const html = read(p);
    assert.match(html, /:focus-visible[^{]*\{[^}]*outline:/, `${p} has a focus-visible outline`);
  }
});

test('every tool page exposes a main landmark', () => {
  for (const p of PAGES) {
    assert.match(read(p), /role="main"|<main\b/, `${p} has a main landmark`);
  }
});

test('the position builders adapt to small screens', () => {
  for (const p of ['stress', 'escape']) {
    assert.match(read(p), /@media \(max-width: 520px\)/, `${p} has a mobile builder layout`);
  }
});

test('interactive inputs still carry accessible labels', () => {
  // spot-check the console + builders keep their aria/label affordances
  assert.match(read('guardian'), /aria-label="Ask Guardian"/);
  assert.match(read('firewall'), /<textarea[^>]*placeholder=/);
});
