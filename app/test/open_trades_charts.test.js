'use strict';
/**
 * Open trades with their chart & engine read — operator ask: "advance the
 * open trades view whit charts and the paterns and eeliotwaves etc visible
 * in open trades and all where we can … also add vwap choc bos and doji".
 *
 * Arena: every open position gets a 📈 expander → SVG candle chart with the
 * position's OWN entry/TP/SL/liq drawn on it, session VWAP + band and
 * BOS/CHoCH computed with the ENGINE'S formulas (RCChartRead), plus the
 * engine's live pattern read (Elliott/Wyckoff chart patterns + doji candle
 * map) from the public /api/patterns proxy. Dashboard: position rows open
 * the symbol modal, which gains a VWAP/structure chip row. Honesty: when
 * the engine bridge is down we say so — never invent a read.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const arena = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const dashHtml = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('arena: every position row carries a chart expander with a colspan detail row', () => {
  assert.match(arena, /chartread\.js\?v=\d+/);
  assert.match(arena, /data-chart="' \+ p\.id/);
  assert.match(arena, /aria-expanded/);
  assert.match(arena, /colspan="9"/);
  // Expanded state survives the 20s tbody rebuild: re-applied in renderAccount.
  assert.match(arena, /if \(chartOpen\[p\.id\]\) paintChart\(p\)/);
  // Toggle re-renders from the LAST payload — no refetch just to expand.
  assert.match(arena, /if \(lastAccount\) renderAccount\(lastAccount\)/);
});

test('arena: the chart carries the position geometry and the engine pattern read', () => {
  assert.match(arena, /entry: p\.entry, sl: p\.sl, tp: p\.tp, liq: p\.liq_price/);
  assert.match(arena, /\/api\/patterns\?symbol=/);
  assert.match(arena, /chart_patterns/);
  assert.match(arena, /candlestick_patterns \|\| pat\.candle_patterns/);
  // VWAP / BOS / CHoCH chips from the shared engine-formula library.
  assert.match(arena, /VWAP '/);
  assert.match(arena, /BOS /);
  assert.match(arena, /CHoCH /);
});

test('arena: honesty — bridge down says so, thin candles say so, never invent', () => {
  assert.match(arena, /engine pattern read unavailable right now/);
  assert.match(arena, /Market candles unavailable right now/);
  assert.match(arena, /not advice/);
});

test('arena: account failures speak — expired session, retry, no silent voids', () => {
  // Operator-reported class: "don't see placed trades" with zero explanation.
  assert.match(arena, /Your session expired/);
  assert.match(arena, /id="acctRetry"/);
  assert.match(arena, /r\.status === 401/);
  // The device-visible error trap registers in its OWN script tag before the
  // main bundle, so even a parse error surfaces as a banner, not a void.
  const trapAt = arena.indexOf("window.addEventListener('error'");
  const mainAt = arena.indexOf('/js/app.js');
  assert.ok(trapAt > 0 && trapAt < mainAt, 'error trap precedes every other script');
  assert.match(arena, /d\.id = 'jsErr'/);
});

test('arena: pattern/candle fetches are cached per symbol (rate-limit friendly)', () => {
  assert.match(arena, /chartData\[sym\]/);
  assert.match(arena, /120000/);
});

test('dashboard: position rows open the symbol drill-down', () => {
  // Trade view table rows and the Portfolio/Home stop-loss items both carry
  // data-sym + role=button, which the existing body delegation turns into
  // an openSymbol() click (and Enter/Space keyboard path).
  assert.match(dash, /<tr data-sym="\$\{esc\(String\(p\.symbol\)\.split\('\/'\)\[0\]\)\}" role="button" tabindex="0"/);
  assert.match(dash, /class="lpos-item" data-sym="\$\{base\}" role="button" tabindex="0"/);
});

test('dashboard: the symbol modal gains a VWAP & structure chip row', () => {
  assert.match(dash, /id="symReadChips"/);
  assert.match(dash, /RCChartRead\.vwap\(parsed\)/);
  assert.match(dash, /RCChartRead\.structure\(parsed\)/);
  assert.match(dash, /CHoCH/);
  const m = dashHtml.match(/dashboard\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 98, `dashboard.js version floor (got ${m && m[1]})`);
  assert.match(dashHtml, /chartread\.js\?v=\d+/);
});

test('website-wide: the modal is the universal decision picture with geometry', () => {
  // openSymbol accepts a caller's geometry and draws it on a full chart.
  assert.match(dash, /async function openSymbol\(rawSym, geo\)/);
  assert.match(dash, /id="symChart"/);
  assert.match(dash, /entry: geo\.e, sl: geo\.sl, tp: geo\.tp/);
  // The delegation (click + keyboard) passes data-geo through.
  assert.match(dash, /openSymbol\(el\.getAttribute\('data-sym'\), _geoOf\(el\)\)/);
  assert.match(dash, /openSymbol\(e\.target\.getAttribute\('data-sym'\), _geoOf\(e\.target\)\)/);
});

test('signals (public): every signal row opens its chart with its own levels', () => {
  assert.match(dash, /data-geo='\$\{esc\(JSON\.stringify\(\{ e: s\.entry_price, sl: s\.stop_loss, tp: s\.take_profit, d: s\.direction \}\)\)\}'/);
  // Position rows pass geometry too (Trade table + Portfolio/Home items).
  assert.match(dash, /\{ e: p\.entry_price, sl: p\.stop_loss, tp: p\.take_profit, d: p\.direction \}/);
});

test('markets (public): at-a-glance read chips under the big chart', () => {
  assert.match(dash, /id="chartRead"/);
  assert.match(dash, /engine formulas/);
  const s = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
  assert.match(s, /\.rc-chart \{ width: 100%/);
  const v = dashHtml.match(/styles\.css\?v=(\d+)/);
  assert.ok(v && Number(v[1]) >= 21, `styles.css version floor (got ${v && v[1]})`);
});
