'use strict';
/**
 * The 3D Strength Map page + its routes. It plots PUBLIC Bitget market data
 * (prices/volume/funding/OI) — market facts, never a user's account or P&L, so
 * it stays §4-clean while still showing real market dollars. Read-only: the
 * "trade" action is a venue picker of external deep links, not an order path.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const market = fs.readFileSync(path.join(__dirname, '..', 'routes', 'market.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strengthmap.html'), 'utf8');
const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'strengthmap.js'), 'utf8');

test('the /strengthmap page + its data routes are wired', () => {
  assert.match(server, /app\.get\('\/strengthmap'.*strengthmap\.html/);
  assert.match(market, /router\.get\('\/strengthmap'/);
  assert.match(market, /router\.get\('\/venues\/:base'/);
  assert.match(market, /buildStrengthMap/);
});

test('the page loads three.js via the vendored import map (no CDN) + a fallback', () => {
  assert.match(html, /<script type="importmap">/);
  assert.match(html, /"three":\s*"\/vendor\/three\/three\.module\.min\.js"/);
  assert.match(html, /type="module"/);
  assert.match(html, /<script nomodule>/);          // graceful no-module message
  assert.match(js, /import 'three'|import\('three'\)/);
  assert.match(js, /renderFallback/);               // 2D fallback when WebGL is absent
});

test('the detail panel offers a CEX/DEX venue picker to open the trade', () => {
  assert.match(js, /\/api\/market\/venues\//);
  assert.match(js, /Open the trade/);
  assert.match(js, /RUNECLAW never auto-routes/);   // §4 recommendations-only disclaimer
});

test('§4: it renders PUBLIC market data only — no user account, P&L or order path', () => {
  // No per-user / money-path surfaces on this public viz.
  assert.ok(!/\/api\/portfolio|\/api\/trades|\/api\/trade\b|equity|net_pnl|balance|live_executor|wallet_address/.test(js),
    'no user account / P&L / order path on the Strength Map');
  // The data comes from the public market endpoints only.
  assert.match(js, /\/api\/market\/strengthmap/);
  assert.match(html, /data-viz, not investment advice|not investment advice/i);
});
