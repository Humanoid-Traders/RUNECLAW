/**
 * UX-4: web onboarding & first-trade (audit-confirmed).
 *
 * 1. A signal's first-trade path used to dead-end at a blank order ticket.
 *    Each still-actionable signal row now carries a one-tap "Trade" button that
 *    stashes the geometry and prefills the Trade view.
 * 2. The 15-item mobile tabbar scrolls behind a hidden scrollbar, so the active
 *    tab could sit off-screen — it's now centered on every view change, with a
 *    right-edge fade cue that more tabs exist.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('actionable signal rows carry a one-tap Trade button with the geometry', () => {
  // Button emitted only for unresolved signals with full geometry.
  assert.match(dash, /const canTrade = s\.pnl == null && s\.entry_price && s\.stop_loss && s\.take_profit/);
  assert.match(dash, /data-ptrade='\$\{esc\(JSON\.stringify\(\{ d: s\.direction, sy: s\.symbol, e: s\.entry_price, sl: s\.stop_loss, tp: s\.take_profit \}\)\)\}'/);
});

test('the Trade button stashes geometry and navigates to the Trade view', () => {
  assert.match(dash, /const pbtn = e\.target\.closest\('\[data-ptrade\]'\)/);
  assert.match(dash, /tradePrefill = JSON\.parse\(pbtn\.getAttribute\('data-ptrade'\)\)/);
  // Navigates via hash (or re-renders if already on the Trade view).
  assert.match(dash, /location\.hash = '#trade'/);
});

test('the Trade view applies the prefill once and then clears it', () => {
  assert.match(dash, /if \(tradePrefill\) \{\s*\n\s*const p = tradePrefill; tradePrefill = null;/);
  // All five geometry fields flow into the ticket inputs.
  for (const id of ['tDir', 'tSym', 'tEntry', 'tSl', 'tTp']) {
    assert.ok(dash.includes(`$('${id}')`), `prefill must populate ${id}`);
  }
  // It nudges the live risk/reward preview.
  assert.match(dash, /\$\('tEntry'\)\.dispatchEvent\(new Event\('input', \{ bubbles: true \}\)\)/);
});

test('module-scoped prefill state exists and starts null', () => {
  assert.match(dash, /let tradePrefill = null;/);
});

test('the active mobile tab is centered on every nav render', () => {
  assert.match(dash, /#tabbarNav a\[aria-current="page"\]/);
  assert.match(dash, /scrollIntoView\(\{ inline: 'center', block: 'nearest' \}\)/);
});

test('the tabbar has a right-edge fade cue (mask-image), not a hard cut', () => {
  assert.match(css, /\.tabbar \{[^}]*mask-image: linear-gradient\(to right/s);
});
