'use strict';
/**
 * Discoverability: the public Strategy-Agent marketplace (/agents) must be
 * reachable AND recognisable from the landing page. It's labelled "Marketplace"
 * (the word visitors look for), linked from both nav blocks + the footer, and
 * surfaced as a live preview section on the page body — not buried in the nav.
 * Source-asserted so it can't silently regress.
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
  // the primary nav link is translatable and labelled "Marketplace"
  assert.match(index, /<a href="\/agents" data-i18n="nav\.agents">Marketplace<\/a>/);
});

test('the nav link reads "Marketplace" across every locale', () => {
  assert.match(i18n, /'nav\.agents':\s*\{\s*en: 'Marketplace'/);
  const m = i18n.match(/'nav\.agents':\s*\{([^}]*)\}/);
  assert.ok(m);
  ['es', 'zh', 'pt', 'fr', 'ar'].forEach(function (loc) {
    assert.ok(new RegExp(loc + ':').test(m[1]), 'nav.agents missing locale ' + loc);
  });
});

test('the landing body surfaces a live marketplace preview section', () => {
  assert.match(index, /id="marketplaceTease"/);
  assert.match(index, /id="marketplaceCards"/);
  // it fetches the real catalogue and links each card into /agents/:slug
  assert.match(index, /\/api\/public\/strategies/);
  assert.match(index, /href="\/agents\/'\s*\+\s*encodeURIComponent\(a\.id\)/);
  // a clear CTA into the full marketplace
  assert.match(index, /href="\/agents"[^>]*data-i18n="sec\.mkt_cta"/);
});

test('the marketplace section strings are translated across locales', () => {
  ['sec.mkt_h', 'sec.mkt_p', 'sec.mkt_cta'].forEach(function (key) {
    const re = new RegExp("'" + key.replace('.', '\\.') + "':\\s*\\{([^}]*)\\}");
    const m = i18n.match(re);
    assert.ok(m, 'missing i18n key ' + key);
    ['en', 'es', 'zh', 'pt', 'fr', 'ar'].forEach(function (loc) {
      assert.ok(new RegExp(loc + ':').test(m[1]), key + ' missing locale ' + loc);
    });
  });
});

test('the marketplace preview shows percent/ratio only — never a dollar figure (§4)', () => {
  // isolate just the marketplace preview IIFE (comment marker → next IIFE) and
  // assert no dollar formatting leaks into it.
  const start = index.indexOf('// Marketplace preview:');
  const end = index.indexOf('// Honest hero:', start);
  assert.ok(start > 0 && end > start, 'could not locate the marketplace preview block');
  const block = index.slice(start, end);
  assert.ok(!/\$\s*[0-9]|fmtMoney|toLocaleString/.test(block), 'no dollar figures in the marketplace preview');
  // it renders percent + ratio metrics
  assert.match(block, /Return|Win rate|PF/);
});

test('the i18n cache-buster was bumped so the new labels ship', () => {
  const m = index.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 3, 'i18n cache-buster is bumped past the baseline');
});
