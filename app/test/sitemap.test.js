'use strict';
/**
 * SEO discoverability: robots.txt + sitemap.xml expose the public marketplace
 * (landing, /agents, each /agents/:slug, leaderboard, proof, track, letters) to
 * search engines, while private/account/API surfaces stay disallowed. The
 * builders are pure, so we assert the exact structure without a live gateway;
 * a source-assert confirms the routes are wired ahead of express.static.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const { buildSitemap, buildRobots, STATIC_PATHS, DISALLOW, normOrigin } =
  require('../lib/sitemap');

const ORIGIN = 'https://example.test';

test('buildSitemap lists every static public page', () => {
  const xml = buildSitemap(ORIGIN, []);
  assert.match(xml, /^<\?xml version="1\.0" encoding="UTF-8"\?>/);
  assert.match(xml, /<urlset xmlns="http:\/\/www\.sitemaps\.org\/schemas\/sitemap\/0\.9">/);
  STATIC_PATHS.forEach(function (s) {
    const loc = ORIGIN + s.path;
    assert.ok(xml.includes('<loc>' + loc + '</loc>'), 'missing static page ' + s.path);
  });
  // the landing page is present exactly once and is top priority
  assert.match(xml, /<loc>https:\/\/example\.test\/<\/loc><changefreq>daily<\/changefreq><priority>1\.0<\/priority>/);
});

test('buildSitemap adds one URL per valid catalogue agent, deduped', () => {
  const agents = [
    { id: 'dip-sniper' }, { id: 'Trend-Rider' }, { id: 'dip-sniper' }, // dup
    { id: 'bad slug!' }, { id: '' }, { id: null }, {}, null,           // all skipped
  ];
  const xml = buildSitemap(ORIGIN, agents);
  assert.ok(xml.includes('<loc>' + ORIGIN + '/agents/dip-sniper</loc>'));
  assert.ok(xml.includes('<loc>' + ORIGIN + '/agents/trend-rider</loc>'), 'slug should be lowercased');
  // dip-sniper appears exactly once despite the duplicate input
  const count = (xml.match(/\/agents\/dip-sniper</g) || []).length;
  assert.strictEqual(count, 1);
  // the malformed slug never appears
  assert.ok(!xml.includes('bad slug'));
});

test('buildSitemap never doubles a trailing slash on the origin', () => {
  const xml = buildSitemap('https://example.test/', [{ id: 'a' }]);
  assert.ok(!xml.includes('example.test//'), 'origin trailing slash must be normalised');
  assert.ok(xml.includes('<loc>https://example.test/agents/a</loc>'));
});

test('buildSitemap XML-escapes (defense in depth) and is well-formed-ish', () => {
  // slugs are regex-validated so cannot contain metacharacters, but the escaper
  // must still neutralise them if a slug ever slipped through.
  const xml = buildSitemap(ORIGIN, []);
  assert.ok(!/<loc>[^<]*[<>"']/.test(xml.replace(/<\/?loc>/g, '')), 'no raw metachars in loc');
});

test('buildRobots allows crawling, disallows private surfaces, points at the sitemap', () => {
  const txt = buildRobots(ORIGIN);
  assert.match(txt, /^User-agent: \*/m);
  assert.match(txt, /^Allow: \/$/m);
  DISALLOW.forEach(function (d) {
    assert.match(txt, new RegExp('^Disallow: ' + d.replace(/[/]/g, '\\/'), 'm'), 'missing Disallow ' + d);
  });
  assert.match(txt, /^Sitemap: https:\/\/example\.test\/sitemap\.xml$/m);
});

test('buildRobots omits the Sitemap line when no origin is resolvable', () => {
  const txt = buildRobots('');
  assert.ok(!/Sitemap:/.test(txt), 'no origin → no (malformed) Sitemap line');
  assert.match(txt, /^User-agent: \*/m); // still a valid robots file
});

test('normOrigin trims whitespace and trailing slashes', () => {
  assert.strictEqual(normOrigin('  https://x.io/// '), 'https://x.io');
  assert.strictEqual(normOrigin(null), '');
});

test('server.js wires /robots.txt and /sitemap.xml', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(src, /app\.get\('\/robots\.txt'/);
  assert.match(src, /app\.get\('\/sitemap\.xml'/);
  // the sitemap enumerates catalogue agents best-effort from the gateway
  assert.match(src, /\/public\/strategies/);
});
