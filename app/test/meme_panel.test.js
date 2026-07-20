'use strict';
/**
 * Meme radar surfacing (PR OO completion) — the backend (lib/meme.js,
 * /api/market/meme, chat intercept) shipped earlier but nothing on the
 * dashboard rendered it: a working feature nobody could see. These pins keep
 * the radar visible and keep its safety-first framing in the panel.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const dash = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('dashboard renders the meme radar panel from /api/market/meme', () => {
  assert.match(dash, /id="p-meme"/, 'panel exists in the Markets view');
  assert.match(dash, /\/api\/market\/meme/, 'panel fetches the real endpoint');
  assert.match(dash, /Ranked by real volume, never by pump %/,
    'anti-shill ranking note is stated to the user');
  assert.match(dash, /extreme/, 'risk tier surfaces in the panel');
});

test('hub one-tap chips include every radar (rwa, airdrop, meme)', () => {
  for (const ask of ['rwa radar', 'airdrop radar', 'meme radar']) {
    assert.ok(dash.includes(`'${ask}'`), `hub chip asks "${ask}"`);
  }
});

test('the meme endpoint the panel targets is actually mounted', () => {
  const market = fs.readFileSync(
    path.join(__dirname, '..', 'routes', 'market.js'), 'utf8');
  assert.match(market, /router\.get\('\/meme'/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/market/);
});
