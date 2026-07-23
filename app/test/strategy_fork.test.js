'use strict';
/**
 * Fork a strategy: a logged-in member can copy any community strategy into their
 * own builder as a NEW draft (no edit id → Save creates their own copy), from a
 * community card's "Fork" button or the /dashboard?fork=<slug> deep link on the
 * shareable page. Closes the creator loop: browse → fork → customize → publish.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const strat = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');

test('the builder shares one field-fill helper for Edit and Fork', () => {
  assert.match(dash, /function fillStratFields\(s\)/);
  // edit path reuses it (no duplicated inline fill)
  assert.match(dash, /_stratEditId = id;\s*fillStratFields\(s\);/);
});

test('fork loads a strategy as a NEW draft (no edit id)', () => {
  assert.match(dash, /function forkInto\(s\)/);
  assert.match(dash, /_stratEditId = null;/);         // saving creates a fresh copy
  assert.match(dash, /function forkBySlug\(slug\)/);
  assert.match(dash, /\/api\/public\/user-strategies\//);  // fetches the source strategy
});

test('community cards expose a Fork button, wired through the view handler', () => {
  assert.match(dash, /data-sfork="\$\{esc\(a\.slug \|\| a\.id\)\}"/);
  assert.match(dash, /const forkBtn = e\.target\.closest\('\[data-sfork\]'\)/);
  assert.match(dash, /forkBySlug\(forkBtn\.getAttribute\('data-sfork'\)\)/);
});

test('the ?fork=<slug> deep link opens the builder pre-forked and clears the param', () => {
  assert.match(dash, /get\('fork'\)/);
  assert.match(dash, /forkBySlug\(_fk\)/);
  assert.match(dash, /history\.replaceState\(null, '', location\.pathname \+ '#agents'\)/);
});

test('the shareable community page links into the fork flow', () => {
  assert.match(strat, /\/dashboard\?fork='/);
  assert.match(strat, /Fork in the app/);
});
