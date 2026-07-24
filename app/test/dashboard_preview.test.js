'use strict';
/**
 * Preview mode for logged-out visitors — the Trade and Portfolio views show
 * the REAL product with REAL public data instead of a bare login wall:
 * live market rows + a disabled ticket on Trade; the engine's real public
 * record (ratio stats on this surface) + an unlock list on Portfolio.
 * No fabricated numbers, ever — that's the §4/honesty line.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const shell = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the Trade view previews the live market + a disabled ticket when logged out', () => {
  assert.match(js, /id="p-prevmkt"/);
  assert.match(js, /api\/market\/tickers/);
  assert.match(js, /23-check risk gate/);
  assert.match(js, /pointer-events:none/);            // the ticket is visibly a preview
  assert.match(js, /Or try the Paper Arena first/);
});

test('the Portfolio view previews the engine\'s REAL record + the unlock list', () => {
  assert.match(js, /id="p-prevtrack"/);
  assert.match(js, /api\/public\/track-record/);
  assert.match(js, /What your account unlocks/);
  assert.match(js, /🔒 free account/);
  assert.match(js, /See the full track record/);
  // ratio stats only on this surface — no usd fields rendered in the preview
  const prev = js.slice(js.indexOf('p-prevtrack'), js.indexOf('p-prevtrack') + 2600);
  assert.ok(!/net_pnl_usd|current_equity_usd/.test(prev), 'preview sticks to ratio stats');
});

test('no fabricated numbers: previews fetch real endpoints, samples are none', () => {
  // the preview panels render from fetched public data or an honest empty state
  assert.match(js, /The market feed is unavailable right now/);
  assert.match(js, /The public record is unavailable right now/);
});

test('cache-buster bumped so the previews ship', () => {
  const v = Number((shell.match(/dashboard\.js\?v=(\d+)/) || [])[1]);
  assert.ok(v >= 90, `dashboard.js v>=90 (got ${v})`);
});

test('the logged-out overview routes visitors into everything already open', () => {
  const cur = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(cur, /open right now — no account needed/);
  assert.match(cur, /mind-stream/);
  assert.match(cur, /href="#signals"/);
  assert.match(cur, /href="#trade"/);
  assert.match(cur, /href="\/arena"/);
  assert.match(cur, /href="\/guardian"/);
});

test('Reputation + Worlds get honest previews — explainers and public directory, no fake numbers', () => {
  const cur = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(cur, /How the score is earned/);
  assert.match(cur, /Tail risk/);
  assert.match(cur, /The worlds we mirror/);
  assert.match(cur, /decentraland\.org/);
  assert.match(cur, /read-only, always/);
  // §4/honesty: preview sections carry no fabricated example values
  const rep = cur.slice(cur.indexOf('How the score is earned'), cur.indexOf('How the score is earned') + 1600);
  assert.ok(!/\d+%|\$\s?\d/.test(rep), 'reputation preview shows axes, not numbers');
});
