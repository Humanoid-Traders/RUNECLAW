'use strict';
/**
 * Public boards polish — trader-card form sparkline (§4: percent-on-margin
 * bars from the card's own public data) and rank-change chips on the Arena
 * board (in-page memory only: shows only moves that actually happened here,
 * never on first paint, reduced-motion neutralized).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const trader = fs.readFileSync(path.join(__dirname, '..', 'public', 'trader.html'), 'utf8');
const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');

test('trader card: recent closes render as a percent sparkline', () => {
  assert.match(trader, /spark-wrap/);
  assert.match(trader, /t\.ret_pct != null/);
  assert.match(trader, /Math\.min\(Math\.abs\(t\.ret_pct\), 60\)/);   // magnitude capped
  assert.match(trader, /last ' \+ seq\.length \+ ' closes · % on margin/);
  // §4: the sparkline reads only the public card's percent field — the
  // builder itself is already asserted dollar-free in arena_trader tests.
  assert.ok(!/vUSDT/.test(trader), 'no virtual-dollar strings on the public card page');
  // Accessible: the SVG is labeled.
  assert.match(trader, /aria-label="Recent closes, percent on margin"/);
});

test('arena board: rank moves show ▲/▼ chips, never on first paint', () => {
  assert.match(arena, /var prevRanks = null;/);
  assert.match(arena, /prevRanks && prevRanks\[x\.handle\] != null && prevRanks\[x\.handle\] !== x\.rank/);
  assert.match(arena, /rk-move/);
  assert.match(arena, /prefers-reduced-motion: reduce\) \{ \.rk-move \{ animation: none/);
});
