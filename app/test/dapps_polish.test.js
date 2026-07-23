'use strict';
/**
 * WEB3-POLISH surface 2 — the dApps directory + Worlds gallery. Browser-only,
 * so source-asserted: the dApp directory gains a live search box (name / purpose
 * / category) with a result count, and both the dApp cards and the Worlds NFT
 * gallery cards gain a hover lift; the NFT thumbnails are enlarged. Read-only:
 * every dApp card links out to the app's own site — nothing here connects,
 * signs, or moves anything.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the dApps view has a live search input wired to the grid only', () => {
  assert.match(dash, /id="dappSearch"/);
  // the matcher now also tests a free-text query across name, purpose, category
  assert.match(dash, /curQ === '' \|\| \(`\$\{d\.name\} \$\{d\.blurb\} \$\{d\.category\}`\)\.toLowerCase\(\)\.includes\(curQ\)/);
  // typing re-paints only the grid (keeps input focus), never the whole control
  assert.match(dash, /if \(!e\.target \|\| e\.target\.id !== 'dappSearch'\) return;/);
  assert.match(dash, /curQ = String\(e\.target\.value \|\| ''\)\.trim\(\)\.toLowerCase\(\);\s*\n\s*paintGrid\(\);/);
});

test('the dApp grid shows a live result count', () => {
  assert.match(dash, /function paintCount\(shown, total\)/);
  assert.match(dash, /shown === total \? `\$\{total\} apps` : `\$\{shown\} of \$\{total\}`/);
  assert.match(dash, /paintCount\(list\.length, total\)/);
});

test('search matching is additive to the existing category + chain filters', () => {
  // the query clause is ANDed onto the prior cat/chain predicate, not replacing it
  assert.match(dash, /\(curCat === 'all' \|\| d\.category === curCat\)[\s\S]*?&& \(curChain === 'all'[\s\S]*?&& \(curQ === ''/);
});

test('dApp + NFT cards gain a reduced-motion-safe hover lift', () => {
  assert.match(css, /\.dapp-card, \.nft-card \{ transition:/);
  assert.match(css, /\.dapp-card:hover, \.dapp-card:focus-visible \{ transform: translateY\(-3px\)/);
  assert.match(css, /\.nft-card:hover \{ transform: translateY\(-2px\)/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{[\s\S]*\.dapp-card, \.nft-card \{ transition: none/);
});

test('the Worlds NFT thumbnails were enlarged from 96px', () => {
  assert.match(dash, /repeat\(auto-fill,minmax\(120px,1fr\)\)/);
  assert.doesNotMatch(dash, /repeat\(auto-fill,minmax\(96px,1fr\)\)/);
});

test('the dApp cards stay read-only external links (never connect/sign in-app)', () => {
  // every card is an anchor that opens the dApp on its own site
  assert.match(dash, /class="dapp-card" href="\$\{esc\(d\.url\)\}" target="_blank" rel="noopener"/);
});

test('cache-busters were bumped so the dApps/Worlds polish ships', () => {
  assert.match(html, /dashboard\.js\?v=(7[6-9]|8\d)/);
  assert.match(html, /styles\.css\?v=18/);
});
