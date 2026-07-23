'use strict';
/**
 * "Twin my real book" — a one-tap link from the Portfolio view into the Stress
 * Lab, seeded from the live positions. §4: the permalink carries only
 * PERCENTAGES (each position's margin share of equity) + leverage + direction —
 * never a dollar figure — so it stays clean even when shared. The encoding
 * matches stress-model.js decodePortfolio.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const M = require('../public/js/stress-model');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the portfolio builds a percent-only Stress Lab book (no dollars)', () => {
  assert.match(dash, /function stressBookFromPortfolio\(pf\)/);
  // weight = margin share of equity → ratios only; leverage + direction encoded
  assert.match(dash, /Number\(p\.size_usd\) \|\| 0\) \/ Math\.max\(1, Number\(p\.leverage\)/);
  const start = dash.indexOf('function stressBookFromPortfolio');
  const body = dash.slice(start, start + 900);
  assert.ok(!/fmtMoney|\$\{[^}]*size_usd|\$[0-9]/.test(body), 'no dollar figure in the encoded book');
});

test('the Portfolio view links into the Digital Twin with the encoded book', () => {
  assert.match(dash, /stressBookFromPortfolio\(pf\)/);
  assert.match(dash, /\/stress\?p=\$\{encodeURIComponent\(book\)\}/);
  assert.match(dash, /Stress-test this book in the Digital Twin/);
});

test('the encoded format decodes cleanly via the shared model', () => {
  // The dashboard emits e.g. "BTC:30:3:L,SOL:10:5:S" — assert the model reads it.
  const decoded = M.decodePortfolio('BTC:30:3:L,SOL:10:5:S');
  assert.deepEqual(decoded, [
    { asset: 'BTC', weight: 30, leverage: 3, dir: 'long' },
    { asset: 'SOL', weight: 10, leverage: 5, dir: 'short' },
  ]);
  // and such a book simulates without any dollar concept
  assert.ok(!/\$/.test(JSON.stringify(M.runAll(decoded))));
});
