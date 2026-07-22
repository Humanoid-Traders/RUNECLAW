'use strict';
/**
 * Mobile / responsive web polish — the phone experience.
 *
 * Browser-rendered CSS/HTML, so source-asserted: the page has a global
 * horizontal-overflow safety net, the headline figure fluid-clamps instead of
 * overflowing a narrow stat card, coarse-pointer glyph buttons get real tap
 * targets, the account grid collapses to one column on a phone, the landing
 * header links stay reachable via a disclosure menu (no dead-end), and the
 * allocation donut is fluid rather than a fixed 200px. Layout only — no logic.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the body has a horizontal-overflow safety net', () => {
  // one stray wide element must never scroll the whole page sideways on a phone.
  assert.match(css, /body\s*\{[\s\S]*?overflow-x:\s*hidden/);
});

test('the headline figure clamps instead of overflowing a narrow stat card', () => {
  // a long currency value on a ~288px card must shrink, not spill.
  assert.match(css, /\.stat \.v\.big\s*\{[\s\S]*?font-size:\s*clamp\([^)]*vw[^)]*\)/);
  assert.match(css, /\.stat \.v\.big\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
});

test('coarse-pointer glyph buttons and compact inputs get real tap targets', () => {
  assert.match(css, /@media \(pointer: coarse\)/);
  // iOS focus-zoom guard PLUS a real min-height on the compact inline inputs.
  assert.match(css, /\.input, select\.input, textarea\.input\s*\{[\s\S]*?font-size:\s*16px[\s\S]*?min-height/);
  // chat header glyphs (close / TTS) get a 40px hit area — coarse-pointer only.
  assert.match(css, /\.chat-close, \.chat-tts\s*\{[\s\S]*?min-height:\s*40px/);
});

test('the account grid collapses to one column on a phone', () => {
  assert.match(html, /@media \(max-width: 520px\)\s*\{\s*\.acct-grid\s*\{\s*grid-template-columns:\s*1fr/);
});

test('the landing header links stay reachable via a mobile disclosure menu', () => {
  // the <640px rule hides the inline links — so a disclosure menu must exist and
  // show in that same range, or the links dead-end on a phone. (page-local CSS.)
  assert.match(html, /@media \(max-width: 639px\)\s*\{[\s\S]*?\.nav-menu\s*\{\s*display:\s*block/);
  assert.match(html, /<details class="nav-menu">/);
  // and it must actually carry the header destinations.
  assert.match(html, /nav-menu-panel[\s\S]*?href="\/dashboard"[\s\S]*?href="\/leaderboard"[\s\S]*?<\/details>/);
});

test('the allocation donut is fluid, not a fixed 200px width', () => {
  assert.match(dash, /id="allocCanvas"[^>]*width:min\(200px,\s*44vw\)/);
  assert.ok(!/id="allocCanvas"[^>]*width:200px;height:200px/.test(dash), 'no fixed 200px canvas');
});
