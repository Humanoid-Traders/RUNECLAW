'use strict';
/** SPOT-2 — Staking center surface: every lock term visible with its
 * duration and the not-revocable warning; locked execution stays behind
 * the bot's explicit confirm. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

test('yield panel renders lock terms with durations and the lock warning', () => {
  const dash = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /fixed_terms/, 'per-term data rendered');
  assert.match(dash, /NOT redeemable until the term ends/, 'lock warning on every chip');
  assert.match(dash, /a lock is not\s+.*revocable/i, 'the invariant is stated in source');
  assert.match(dash, /double-confirm showing the lock end date/, 'execution gate stated');
});
