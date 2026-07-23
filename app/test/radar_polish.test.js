'use strict';
/**
 * WEB3-POLISH surface 1 — the RWA / meme / airdrop radars in the Markets view.
 * Browser-only DOM code, so source-asserted: the radar tables gain a momentum
 * spark-bar, the airdrop status vocabulary is richer + colour-coded, and a
 * jump-nav lets a visitor scroll straight to any radar panel. Visualization &
 * market-intelligence only — nothing here trades, and no dollar figure is put
 * on any spark (percent momentum only, §4).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('sparkBar helper exists, caps magnitude, and is decorative (aria-hidden)', () => {
  assert.match(dash, /function sparkBar\(pct, cap = 20\)/);
  // magnitude is clamped so a +400% pump does not dwarf every other row
  assert.match(dash, /Math\.min\(Math\.abs\(Number\(pct\)\), cap\)/);
  assert.match(dash, /class="spark spark--\$\{dir\}" aria-hidden="true"/);
});

test('the spark-bar is wired into both the RWA and meme 24h cells', () => {
  // RWA category token row + meme token row each append a spark after the %.
  const hits = dash.match(/\}%\$\{sparkBar\(t\.change_24h_pct\)\}/g) || [];
  assert.ok(hits.length >= 1, 'RWA % cell renders a spark bar');
  assert.match(dash, /: '—'\}\$\{sparkBar\(t\.change_24h_pct\)\}/);  // meme cell (nullable %)
});

test('the spark bar is styled and reduced-motion safe', () => {
  assert.match(css, /\.spark \{/);
  assert.match(css, /\.spark--up\s+\.spark-fill \{ background: var\(--up\)/);
  assert.match(css, /\.spark--down \.spark-fill \{ background: var\(--down\)/);
  // width is the only animated property; reduced-motion kills the transition.
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.spark-fill \{ transition: none/);
});

test('airdrop status vocabulary is richer and uses the shared chip system', () => {
  // The old flat badge map is gone; the new one is colour-coded chips.
  assert.doesNotMatch(dash, /live: '<span class="badge" style="color:var\(--up\)">live<\/span>'/);
  for (const s of ['live', 'points', 'snapshot', 'testnet', 'confirmed', 'ended']) {
    assert.ok(dash.includes(`${s}:`), `airdrop status "${s}" is mapped`);
  }
  assert.match(dash, /return `<span class="chip \$\{cls\}">\$\{esc\(label\)\}<\/span>`/);
});

test('the Markets view has a radar jump-nav wired to the panels', () => {
  assert.match(dash, /const MARKET_JUMPS = \[/);
  assert.match(dash, /class="jumpnav" aria-label="Jump to radar"/);
  assert.match(dash, /data-jump="\$\{id\}"/);
  // click handler is delegated (leak-safe) and scrolls + flashes the target.
  assert.match(dash, /e\.target\.closest\('\[data-jump\]'\)/);
  assert.match(dash, /getElementById\('p-' \+ b\.getAttribute\('data-jump'\)\)/);
  assert.match(dash, /el\.classList\.remove\('sec-flash'\)/);
  // jump-nav is styled
  assert.match(css, /\.jumpnav \{ display: flex/);
});

test('cache-busters were bumped so the polish actually ships', () => {
  assert.match(html, /dashboard\.js\?v=7[45789]/);
  assert.match(html, /styles\.css\?v=1[5-9]/);
});

test('§4: the spark visualises percent momentum, never a dollar amount', () => {
  // Pull the sparkBar body and assert it never renders a money figure — no
  // literal '$' string and no money formatter. (Template `${}` syntax is fine.)
  const m = dash.match(/function sparkBar\([\s\S]*?\n  \}/);
  assert.ok(m, 'sparkBar body found');
  assert.ok(!/'\$'|"\$"|\$\$\{/.test(m[0]), 'spark bar must not render a literal dollar sign');
  assert.ok(!/fmtK|fmtMoney|fmtPrice/.test(m[0]), 'spark bar must not use a money formatter');
});
