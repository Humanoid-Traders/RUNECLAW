'use strict';
/**
 * The public /intent page — the standalone Intent Compiler demo. Verifies the
 * route is served, the page loads the pure model, cross-links back into
 * Guardian, and honours §4 (compile-only, no funds, no dollar figures in the
 * static copy). The compiler logic itself is covered in intent_model.test.js.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'intent.html'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

test('the /intent route is served', () => {
  assert.match(server, /app\.get\('\/intent'/);
  assert.match(server, /intent\.html/);
});

test('the page loads the intent-model and mounts the compiler UI', () => {
  assert.match(html, /js\/intent-model\.js/);
  assert.match(html, /IntentModel/);
  assert.match(html, /id="intent"/);        // the textarea
  assert.match(html, /id="rules"/);         // the compiled envelope
  assert.match(html, /id="coverage"/);      // the coverage meter
});

test('it cross-links back into Guardian', () => {
  assert.match(html, /href="\/guardian"/);
});

test('§4: the static copy makes the compile-only, no-funds posture explicit', () => {
  assert.match(html, /binds nothing|compile preview|compiler preview|moves no funds/i);
  assert.match(html, /not investment advice/i);
  assert.match(html, /revocable/i);
});

test('§4: the static page copy shows no dollar figure', () => {
  // A public surface — no "$1,000"-style amounts in the shipped HTML copy.
  assert.ok(!/\$\s?\d/.test(html), 'no $-amount in the page copy');
});

test('it has a main landmark and a keyboard-focus ring (a11y)', () => {
  assert.match(html, /role="main"|<main\b/);
  assert.match(html, /:focus-visible[^{]*\{[^}]*outline:/);
});
