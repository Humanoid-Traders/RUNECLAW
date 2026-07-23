'use strict';
/**
 * Order ticket — live-price draft levels. A coin arriving from the Strength
 * Map (or the "Suggest from live price" button) should land as an editable
 * DRAFT (entry/stop/target from the live price + 24h range), not three empty
 * "0.00" fields. §4: draft only — nothing is placed, the risk gate stays the
 * authority, and the user edits every number.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the ticket exposes a "Suggest from live price" control wired to a prefill', () => {
  assert.match(dash, /id="tSuggestBtn"/);
  assert.match(dash, /Suggest from live price/);
  assert.match(dash, /getElementById\('tSuggestBtn'\)\.addEventListener/);
  assert.match(dash, /async function prefillFromLive/);
});

test('prefill reads the live ticker (price + 24h range) and drafts levels', () => {
  assert.match(dash, /function draftLevels\(price, high, low, dir\)/);
  assert.match(dash, /parseFloat\(t\.lastPr\)/);
  assert.match(dash, /t\.high24h/);
  assert.match(dash, /t\.low24h/);
  // stop distance ~half the day's range, bounded to a sane 1–5%.
  assert.match(dash, /Math\.min\(0\.05, Math\.max\(0\.01/);
});

test('force=false never clobbers a number the user already typed', () => {
  assert.match(dash, /const setIf = \(id, v\) => \{ const el = \$\(id\); if \(el && \(force \|\| !el\.value\)\)/);
});

test('the Strength Map deep-link drafts from the live price on arrival', () => {
  assert.match(dash, /get\('trade'\)/);
  assert.match(dash, /prefillFromLive\(false\)/);
  assert.match(dash, /drafted at the live price/);
  assert.match(dash, /Nothing is placed yet/);       // §4 safety framing
});

test('the dashboard.js cache-buster is bumped so the prefill ships', () => {
  assert.match(html, /dashboard\.js\?v=\d\d+/);
});

// Replicated invariant check of the draft-levels math (documents intent):
// a LONG draft puts the stop below entry and the target above (mirror for
// SHORT), with reward:risk ~1.8 and the stop bounded to 1–5%.
test('draft geometry is valid for both directions (replicated math)', () => {
  function draftLevels(price, high, low, dir) {
    if (!(price > 0)) return null;
    const rangePct = (high > 0 && low > 0 && high > low) ? (high - low) / price : 0.03;
    const stopPct = Math.min(0.05, Math.max(0.01, rangePct * 0.5));
    const rr = 1.8;
    const long = dir !== 'SHORT';
    const stop = long ? price * (1 - stopPct) : price * (1 + stopPct);
    const target = long ? price * (1 + stopPct * rr) : price * (1 - stopPct * rr);
    return { entry: price, sl: stop, tp: target, stopPct };
  }
  const L = draftLevels(100, 104, 98, 'LONG');
  assert.ok(L.sl < L.entry && L.tp > L.entry, 'long: stop below, target above');
  const S = draftLevels(100, 104, 98, 'SHORT');
  assert.ok(S.sl > S.entry && S.tp < S.entry, 'short: stop above, target below');
  // reward:risk ≈ 1.8 both ways
  assert.ok(Math.abs((Math.abs(L.tp - L.entry) / Math.abs(L.entry - L.sl)) - 1.8) < 1e-9);
  // stop bounded even for a wild 40% day range
  const wide = draftLevels(100, 130, 90, 'LONG');
  assert.equal(wide.stopPct, 0.05);
  // and floored for a dead-flat day
  const flat = draftLevels(100, 100, 100, 'LONG');
  assert.equal(flat.stopPct, 0.015);
});
