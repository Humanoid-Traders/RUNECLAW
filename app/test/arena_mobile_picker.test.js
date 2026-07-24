'use strict';
/**
 * Arena mobile ticket fix — operator-reported from production Android:
 * "papertrade is not work dont see placed trades and only btc is to choose".
 * Root cause was client-side: the symbol picker was a bare <datalist>, whose
 * suggestions are unreliable on Android (reads as "only BTC exists"), and a
 * hand-typed bare base ("SOL") 400s server-side, so nothing fills and the
 * positions table stays empty. Backend verified healthy end-to-end.
 *
 * Fixes under test: tap-to-pick symbol chips (work everywhere), client-side
 * symbol normalization mirroring the server, a real-symbol majors fallback
 * so the picker never collapses to the default, and scroll-to-positions
 * after a fill so "did it work?" is never a question.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');

test('tap-to-pick chips exist and scroll horizontally on touch', () => {
  assert.match(html, /id="symChips"/);
  assert.match(html, /\.sym-chips \{ display: flex; gap: 6px; overflow-x: auto/);
  assert.match(html, /data-sym-chip/);
  // Tapping a chip fills the input and re-triggers the mark fetch.
  assert.match(html, /\$\('tSym'\)\.value = b\.getAttribute\('data-sym-chip'\)/);
  assert.match(html, /dispatchEvent\(new Event\('input'\)\)/);
});

test('typed symbols are normalized like the server expects', () => {
  assert.match(html, /function normSym\(/);
  assert.match(html, /if \(v && !\/USDT\$\/\.test\(v\)\) v \+= 'USDT';/);
  // Both the open order and the mark fetch go through it.
  assert.match(html, /symbol: normSym\(\$\('tSym'\)\.value\)/);
  assert.match(html, /var sym = normSym\(\$\('tSym'\)\.value\);/);
});

test('the picker never collapses to just BTC — real majors as the floor', () => {
  assert.match(html, /MAJOR_SYMS = \['BTCUSDT', 'ETHUSDT', 'SOLUSDT'/);
  assert.match(html, /if \(!syms\.length\) syms = MAJOR_SYMS\.slice\(\);/);
});

test('a fill scrolls the positions table into view (reduced-motion aware)', () => {
  assert.match(html, /\$\('posPanel'\)\.scrollIntoView/);
  assert.match(html, /reduce \? 'auto' : 'smooth'/);
});
