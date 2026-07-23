'use strict';
/**
 * Discoverability: the public Strategy-Agent marketplace (/agents) must be
 * reachable from the landing page — a visitor should never have to know the URL.
 * Source-asserted: the landing links to /agents in both nav blocks and the
 * footer, the i18n key exists (so the label translates), and the i18n cache
 * buster was bumped.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the landing links to the public /agents marketplace in nav + footer', () => {
  // Two nav blocks (primary + mobile menu) + the footer = at least 3 links.
  const links = index.match(/href="\/agents"[^>]*>/g) || [];
  assert.ok(links.length >= 3, `expected /agents linked in nav (x2) + footer, found ${links.length}`);
  // the primary nav link is translatable
  assert.match(index, /<a href="\/agents" data-i18n="nav\.agents">Agents<\/a>/);
});

test('the nav.agents i18n key exists across locales', () => {
  assert.match(i18n, /'nav\.agents':\s*\{\s*en: 'Agents'/);
  // has the same locale set as its siblings (es/zh/pt/fr/ar)
  const m = i18n.match(/'nav\.agents':\s*\{([^}]*)\}/);
  assert.ok(m);
  ['es', 'zh', 'pt', 'fr', 'ar'].forEach(function (loc) {
    assert.ok(new RegExp(loc + ':').test(m[1]), 'nav.agents missing locale ' + loc);
  });
});

test('the i18n cache-buster was bumped so the new label ships', () => {
  assert.match(index, /i18n\.js\?v=[2-9]/);
});
