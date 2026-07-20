'use strict';
/**
 * Discoverability pins — every shipped public surface must be reachable from
 * the site's main navigation. These are the links a full-chain audit found
 * missing ("the page works but nobody can find it"); pin them so a nav
 * refactor can't silently orphan a surface again.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');

test('landing page links every public surface from nav and footer', () => {
  const html = pub('index.html');
  for (const href of ['/dashboard', '/track', '/proof', '/leaderboard', '/letter']) {
    const hits = html.split(`href="${href}"`).length - 1;
    assert.ok(hits >= 2, `${href} should appear in both topnav and footer (found ${hits})`);
  }
  assert.match(html, /data-i18n="nav\.letter"/, 'letter nav link is translatable');
});

test('dashboard letter panel links the public /letter archive', () => {
  const js = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(js, /href="\/letter"/, 'public letter archive reachable from the dashboard');
});

test('proof page links the ERC-8004 identity card when an agent address exists', () => {
  const html = pub('proof.html');
  assert.match(html, /\/agent\/\$\{esc\(addr\.toLowerCase\(\)\)\}/,
    'agent card link built from the publication address');
  assert.match(html, /\^0x\[0-9a-fA-F\]\{40\}\$/,
    'address validated before a link is rendered');
});
