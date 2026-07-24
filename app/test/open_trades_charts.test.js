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
  assert.ok(m && Number(m[1]) >= 97, `dashboard.js version floor (got ${m && m[1]})`);
  assert.match(dashHtml, /chartread\.js\?v=\d+/);
});
