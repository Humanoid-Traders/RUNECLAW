/**
 * UX-1: animation correctness (from the 2026-07 UX audit — every item below
 * was a verified defect: live pushes tore the whole view down, timers
 * blanked loaded panels to skeletons, and several designed animations were
 * dead because no JS or the wrong selector referenced them).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');
const css = read('styles.css');
const dash = read('js/dashboard.js');
const app = read('js/app.js');

test('SSE live pushes refresh in place (soft), never a full teardown', () => {
  // Every stream handler that re-renders the current view must pass soft mode…
  const sse = dash.slice(dash.indexOf('connectStream({'), dash.indexOf('activity: onActivity'));
  const rerenders = (sse.match(/showView\(/g) || []).length;
  const soft = (sse.match(/\{ soft: true \}/g) || []).length;
  assert.ok(rerenders >= 4 && soft === rerenders,
    `all ${rerenders} SSE re-renders must be soft (got ${soft})`);
  // …and soft mode suppresses scroll-jump + entrance replay (the guard may be
  // a one-liner or a braced block that also triggers the view cross-fade).
  assert.match(dash, /if \(!opts\.soft\)[\s{]*window\.scrollTo/);
  assert.match(css, /\.rc-soft \.panel, \.rc-soft \.rc-rise \{ animation: none; \}/);
});

test('loaded panels refresh in place — no skeleton flash on timers', () => {
  assert.match(app, /el\.dataset\.rcLoaded === '1'/);
  assert.match(app, /if \(hasContent\) return;\s*\/\/ stale beats blank/);
});

test('panel hover lift is alive: entrance uses backwards, not both', () => {
  assert.ok(css.includes('.panel { animation: rc-rise .42s cubic-bezier(.2, .7, .3, 1) backwards; }'),
    "fill-mode 'both' pins transform forever and kills .panel:hover translateY");
  assert.ok(css.includes('.rc-rise { animation: rc-rise .4s cubic-bezier(.2, .7, .3, 1) backwards; }'));
});

test('reduced-motion guard also stops infinite loops', () => {
  const guard = css.slice(css.indexOf('prefers-reduced-motion'), css.indexOf('prefers-reduced-motion') + 400);
  assert.match(guard, /animation-iteration-count: 1 !important/);
});

test('equity flash classes are actually wired to fresh ticks', () => {
  assert.match(dash, /rc-flash-up' : ' rc-flash-down'/);
  assert.match(dash, /_rcLastEquity/);
});

test('new live-feed events rise in instead of popping', () => {
  const insert = dash.indexOf("insertAdjacentHTML('afterbegin', feedItemHtml(ev))");
  assert.ok(insert > 0);
  assert.ok(dash.slice(insert, insert + 300).includes("classList.add('rc-rise')"));
});

test('modals and the chat drawer animate open', () => {
  assert.match(css, /@keyframes rc-modal-in/);
  assert.ok(css.includes('.modal-card {') && /modal-card \{[^}]*rc-modal-in/.test(css));
  assert.ok(/chat-drawer \{[^}]*rc-modal-in/s.test(css));
});

test('active-nav glow targets aria-current (which the router actually sets)', () => {
  assert.match(css, /a\[aria-current="page"\] \.icon/);
});

test('LIVE chip ring pulses in the chip’s own color family', () => {
  assert.match(css, /rc-live-ring \{\s*0% \{ box-shadow: 0 0 0 0 color-mix\(in srgb, var\(--down\)/);
});
