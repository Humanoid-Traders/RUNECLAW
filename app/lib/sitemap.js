'use strict';
/**
 * Pure builders for robots.txt + sitemap.xml so the PUBLIC marketplace is
 * discoverable by search engines: the landing page, the Strategy-Agent
 * directory (/agents), every per-agent page (/agents/:slug), the leaderboard,
 * Proof of PnL, the track record, and the agent letters.
 *
 * §4-safe: these are link maps only — no dollar figures, no per-user or private
 * surfaces (dashboard / account / reset / verify / api are disallowed), and the
 * agent slugs come from the same published catalogue the public pages already
 * render. Builders are pure so the route can cache their output and tests can
 * assert the exact XML/text without a live gateway.
 */

// A catalogue slug — matches the public per-agent route's own validation.
const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;

// Public, indexable pages that exist regardless of the live catalogue.
const STATIC_PATHS = [
  { path: '/', changefreq: 'daily', priority: '1.0' },
  { path: '/agents', changefreq: 'daily', priority: '0.9' },
  { path: '/leaderboard', changefreq: 'daily', priority: '0.8' },
  { path: '/proof', changefreq: 'weekly', priority: '0.7' },
  { path: '/track', changefreq: 'weekly', priority: '0.7' },
  { path: '/letter', changefreq: 'weekly', priority: '0.6' },
  { path: '/developers', changefreq: 'monthly', priority: '0.5' },
  { path: '/status', changefreq: 'daily', priority: '0.4' },
];

// Private / account / API surfaces crawlers should never index.
const DISALLOW = ['/api/', '/dashboard', '/reset', '/verify', '/wallet-link'];

function xmlEscape(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

// Strip a trailing slash so `origin + path` never doubles it.
function normOrigin(origin) {
  return String(origin == null ? '' : origin).trim().replace(/\/+$/, '');
}

function urlNode(origin, p, meta) {
  const loc = xmlEscape(origin + p);
  const cf = meta && meta.changefreq
    ? '<changefreq>' + meta.changefreq + '</changefreq>' : '';
  const pr = meta && meta.priority
    ? '<priority>' + meta.priority + '</priority>' : '';
  return '<url><loc>' + loc + '</loc>' + cf + pr + '</url>';
}

/**
 * buildSitemap(origin, agents) → the full sitemap.xml string.
 * `agents` is the catalogue array ({ id, ... }); non-matching / duplicate slugs
 * are skipped. With an empty/absent catalogue the static pages still ship.
 */
function buildSitemap(origin, agents) {
  const o = normOrigin(origin);
  const nodes = STATIC_PATHS.map(function (s) { return urlNode(o, s.path, s); });

  const seen = new Set();
  (Array.isArray(agents) ? agents : []).forEach(function (a) {
    const slug = a && String(a.id == null ? '' : a.id).toLowerCase();
    if (!slug || !SLUG_RE.test(slug) || seen.has(slug)) return;
    seen.add(slug);
    nodes.push(urlNode(o, '/agents/' + slug, { changefreq: 'weekly', priority: '0.8' }));
  });

  return '<?xml version="1.0" encoding="UTF-8"?>\n'
    + '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    + nodes.join('\n') + '\n</urlset>\n';
}

/** buildRobots(origin) → robots.txt pointing crawlers at the sitemap. */
function buildRobots(origin) {
  const o = normOrigin(origin);
  const lines = ['User-agent: *', 'Allow: /'];
  DISALLOW.forEach(function (d) { lines.push('Disallow: ' + d); });
  lines.push('');
  if (o) lines.push('Sitemap: ' + o + '/sitemap.xml');
  lines.push('');
  return lines.join('\n');
}

module.exports = { buildSitemap, buildRobots, STATIC_PATHS, DISALLOW, SLUG_RE, normOrigin };
