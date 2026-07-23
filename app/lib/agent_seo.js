'use strict';
/**
 * Server-rendered per-agent <head> metadata for /agents/:slug.
 *
 * The strategy page body is client-rendered (it fetches /api/public/strategies),
 * so without this every agent shared ONE generic social card and one generic
 * search snippet. This injects a per-agent <title>, meta description, canonical
 * link, Open Graph + Twitter tags, and JSON-LD structured data — so each agent
 * unfurls on social and ranks in search with its own identity.
 *
 * §4-safe: title/description carry the agent's NAME, TAGLINE and DESIGN only —
 * never a dollar figure. (The verified backtest percentages already live in the
 * page body; the head meta stays qualitative.)
 *
 * Pure functions so the route can cache and tests can assert the exact output.
 */

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;
const MARKER = '<!--AGENT_SEO-->';
const DEFAULT_IMAGE = '/og_image_1200x630.jpg';
const MAX_DESC = 200;

function attrEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// Escape a string for safe inline JSON-LD: JSON.stringify plus neutralise the
// sequence that could close the <script> element early.
function jsonEsc(obj) {
  return JSON.stringify(obj).replace(/</g, '\\u003c').replace(/>/g, '\\u003e')
    .replace(/&/g, '\\u0026');
}

function normOrigin(o) {
  return String(o == null ? '' : o).trim().replace(/\/+$/, '');
}

function clamp(s, n) {
  const str = String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
  return str.length <= n ? str : (str.slice(0, n - 1).replace(/\s+\S*$/, '') + '…');
}

function metaBlock(m) {
  const title = attrEsc(m.title);
  const desc = attrEsc(m.desc);
  const url = attrEsc(m.url);
  const image = attrEsc(m.image);
  const jsonld = {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: m.title,
    description: m.desc,
    url: m.url,
    isPartOf: { '@type': 'WebSite', name: 'RUNECLAW', url: m.siteUrl },
  };
  return [
    '<title>' + title + '</title>',
    '<meta name="description" content="' + desc + '">',
    '<link rel="canonical" href="' + url + '">',
    '<meta property="og:title" content="' + title + '">',
    '<meta property="og:description" content="' + desc + '">',
    '<meta property="og:type" content="website">',
    '<meta property="og:url" content="' + url + '">',
    '<meta property="og:image" content="' + image + '">',
    '<meta property="og:image:width" content="1200">',
    '<meta property="og:image:height" content="630">',
    '<meta name="twitter:card" content="summary_large_image">',
    '<meta name="twitter:title" content="' + title + '">',
    '<meta name="twitter:description" content="' + desc + '">',
    '<meta name="twitter:image" content="' + image + '">',
    '<script type="application/ld+json">' + jsonEsc(jsonld) + '</script>',
  ].join('\n');
}

// Generic (no agent matched / catalogue unavailable): the directory-level card.
function genericMeta(origin) {
  const o = normOrigin(origin);
  return metaBlock({
    title: 'RUNECLAW — Strategy Agents',
    desc: 'Real engine presets, each with a verified, reproducible backtest. '
      + 'Follow one, reproduce its numbers in the Lab, or ask it anything.',
    url: o + '/agents',
    image: o + DEFAULT_IMAGE,
    siteUrl: o || '/',
  });
}

// Per-agent card: name + tagline + how-it-trades, design/regime only, no $.
function agentMeta(agent, origin, slug) {
  const o = normOrigin(origin);
  const name = (agent && agent.name) ? String(agent.name) : slug;
  const tagline = (agent && agent.tagline) ? String(agent.tagline) : '';
  const how = (agent && agent.how) ? String(agent.how) : '';
  const base = tagline || ((agent && agent.community)
    ? ('The ' + name + ' strategy — a community-authored RUNECLAW config (intent rules, no dollar figures).')
    : ('The ' + name + ' strategy agent — a real RUNECLAW engine preset with a verified, reproducible backtest.'));
  const desc = clamp(how ? (base + ' ' + how) : base, MAX_DESC);
  return metaBlock({
    title: 'RUNECLAW — ' + name,
    desc,
    url: o + '/agents/' + slug,
    image: o + DEFAULT_IMAGE,
    siteUrl: o || '/',
  });
}

/**
 * injectAgentMeta(html, agent, origin, slug) → html with the AGENT_SEO marker
 * replaced by per-agent meta (or the generic directory card when agent is null).
 * If the marker is absent the html is returned unchanged.
 */
function injectAgentMeta(html, agent, origin, slug) {
  const meta = agent ? agentMeta(agent, origin, slug) : genericMeta(origin);
  return String(html).replace(MARKER, meta);
}

module.exports = { injectAgentMeta, agentMeta, genericMeta, metaBlock, clamp, normOrigin, SLUG_RE, MARKER };
