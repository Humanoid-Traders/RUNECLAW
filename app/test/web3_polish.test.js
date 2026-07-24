'use strict';
/**
 * Web3 pro-feel polish — the dashboard's net-worth / holdings / wallet-mirror /
 * DeFi cluster upgrades from key-value dumps and raw tables to a card system:
 * a net-worth hero number, typed source cards with chips, per-asset rows with
 * token glyphs + REAL 24h deltas (public tickers — never invented numbers),
 * and protocol cards. §4: these are private per-user surfaces (dollars OK);
 * public surfaces are untouched.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const shell = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('net worth leads with a hero headline number', () => {
  assert.match(js, /w3-hero/);
  assert.match(js, /Real net worth/);
  // the paper-exclusion honesty note stays
  assert.match(js, /paper excluded/i);
});

test('holdings + wallet mirror + DeFi render the card system with chips', () => {
  assert.match(js, /srcCard\(/);                  // holdings source cards
  assert.match(js, /protoCard\(/);                // DeFi protocol cards
  const cardUses = (js.match(/w3-card/g) || []).length;
  assert.ok(cardUses >= 3, `w3-card used across the cluster (found ${cardUses})`);
  assert.match(js, /chip chip--up/);              // on-chain chips
  assert.match(js, /chip--info/);                 // exchange/protocol chips
});

test('asset deltas come from the real public ticker feed, best-effort', () => {
  assert.match(js, /api\/market\/tickers/);
  assert.match(js, /change24h/);
  assert.match(js, /w3-delta/);
  // best-effort: a ticker failure must not sink the balances render
  assert.match(js, /deltas are decoration/);
});

test('the stylesheet ships the w3 card system', () => {
  for (const cls of ['.w3-hero', '.w3-card', '.w3-card .tok', '.w3-delta']) {
    assert.ok(css.includes(cls), `styles.css has ${cls}`);
  }
  // mobile: cards wrap instead of overflowing
  assert.match(css, /@media \(max-width: 520px\) \{ \.w3-card/);
});

test('cache-busters were bumped so the polish actually ships', () => {
  const cssV = Number((shell.match(/styles\.css\?v=(\d+)/) || [])[1]);
  const jsV = Number((shell.match(/dashboard\.js\?v=(\d+)/) || [])[1]);
  assert.ok(cssV >= 19, `styles.css v>=19 (got ${cssV})`);
  assert.ok(jsV >= 88, `dashboard.js v>=88 (got ${jsV})`);
});

test('honesty invariants survive: unreadable stays unreadable, unpriced stays unpriced', () => {
  assert.match(js, /unreadable/);
  assert.match(js, /unpriced/);
});
