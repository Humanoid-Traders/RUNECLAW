'use strict';
/**
 * Quality-hardening (cont.) — a `main` landmark on the remaining standalone
 * public pages, so assistive tech can jump straight to content everywhere. The
 * six Guardian/tool pages were covered in tool_pages_a11y; this covers the rest.
 * The shared styles.css already provides the global :focus-visible ring.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const PAGES = ['strategy', 'status', 'agents', 'leaderboard', 'developers', 'letter', 'wallet-link'];

test('every remaining standalone page exposes a main landmark', () => {
  for (const p of PAGES) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'public', p + '.html'), 'utf8');
    assert.match(html, /role="main"|<main\b/, `${p}.html has a main landmark`);
  }
});

test('the shared stylesheet still ships the global keyboard-focus ring', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
  assert.match(css, /:focus-visible\s*\{[^}]*outline:/);
});
