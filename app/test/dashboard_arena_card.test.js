'use strict';
/**
 * Dashboard Arena card — the practice account beside the real one in the
 * portfolio view. Private per-user surface (§4: virtual dollars fine); links
 * into /arena, shows follow state + earned badges, and pitches the Arena in
 * its empty state.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const shell = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the portfolio view mounts an Arena panel wired to the account API', () => {
  assert.match(js, /id="p-arena"/);
  assert.match(js, /id="c-arena"/);
  assert.match(js, /api\/arena\/account/);
  assert.match(js, /Arena return/);
  assert.match(js, /href="\/arena"/);
  assert.match(js, /⚡ following/);          // follow state chip
  assert.match(js, /virtual/);               // honesty badge on the panel title
});

test('cache-buster bumped so the card ships', () => {
  const v = Number((shell.match(/dashboard\.js\?v=(\d+)/) || [])[1]);
  assert.ok(v >= 89, `dashboard.js v>=89 (got ${v})`);
});
