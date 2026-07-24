'use strict';
/**
 * WEB3-POLISH surface 4 — the wallet / net-worth / DeFi panels in the Portfolio
 * view. Browser-only, so source-asserted: the net-worth panel gains a
 * proportional composition bar (connected exchange vs on-chain wallet), and each
 * Aave position gains a liquidation-risk meter beside its health factor. Both
 * are read-only, real-data-derived, and decorative (aria-hidden) — the numbers
 * beside them stay the source of truth.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('stackBar builds a proportional segmented bar from positive parts only', () => {
  assert.match(dash, /function stackBar\(parts\)/);
  // filter to positive values, widths normalised to the total
  assert.match(dash, /\.filter\(function \(p\) \{ return p && Number\(p\.value\) > 0; \}\)/);
  assert.match(dash, /var pct = \(Number\(p\.value\) \/ total\) \* 100;/);
  assert.match(dash, /class="stackbar" aria-hidden="true"/);
});

test('the net-worth panel renders the composition bar from cex + wallet', () => {
  assert.match(dash, /const composition = stackBar\(\[/);
  assert.match(dash, /value: \(c && c\.ok \? c\.equity_usd : 0\)/);
  assert.match(dash, /value: \(w && w\.linked \? w\.total_usd : 0\)/);
  assert.match(dash, /\+ composition/);
});

test('hfMeter is a clamped 0-3 liquidation gauge coloured by the HF thresholds', () => {
  assert.match(dash, /function hfMeter\(hf\)/);
  assert.match(dash, /Math\.max\(0, Math\.min\(Number\(hf\), 3\)\) \/ 3/);
  // same danger/warn/safe cut-points as the numeric HF class beside it
  assert.match(dash, /Number\(hf\) < 1\.1 \? 'down' : Number\(hf\) < 1\.5 \? 'warn' : 'up'/);
  assert.match(dash, /class="hfmeter hfmeter--' \+ cls/);
});

test('the DeFi panel renders the HF meter next to each Aave health factor', () => {
  assert.match(dash, /HF <span class="\$\{hfCls\}">\$\{a\.health_factor\}<\/span>\$\{hfMeter\(a\.health_factor\)\}/);
});

test('both meters are styled and reduced-motion safe', () => {
  assert.match(css, /\.stackbar \{ display: flex/);
  assert.match(css, /\.stackbar-seg\.chip--up \{ background: var\(--up\)/);
  assert.match(css, /\.hfmeter--down \.hfmeter-fill \{ background: var\(--down\)/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.stackbar-seg \{ transition: none/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.hfmeter-fill \{ transition: none/);
});

test('§4: the composition bar shows a percent split — its segments carry no $', () => {
  // Pull the stackBar body; it must derive widths from percentages only, with
  // no money formatter smuggled in. Dollars stay in the kv-rows above it.
  const m = dash.match(/function stackBar\(parts\)[\s\S]*?\n  \}/);
  assert.ok(m, 'stackBar body found');
  assert.ok(!/fmtMoney|fmtK|'\$'|"\$"/.test(m[0]), 'composition bar must not render a dollar figure');
});

test('cache-busters were bumped so the wallet polish ships', () => {
  { const v = Number((html.match(/dashboard\.js\?v=(\d+)/) || [])[1]); assert.ok(v >= 75, `dashboard.js v>=75 (got ${v})`); }
  assert.match(html, /styles\.css\?v=(1[7-9]|[2-9]\d)/);
});
