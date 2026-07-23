'use strict';
/**
 * Per-agent SEO / social cards for /agents/:slug. Each strategy agent must get
 * its OWN <title>, description, canonical, Open Graph, Twitter card and JSON-LD
 * — injected server-side into the AGENT_SEO marker — never a dollar figure (§4).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const seo = require('../lib/agent_seo');
const ORIGIN = 'https://example.test';

const AGENT = {
  id: 'dip-sniper',
  name: 'Dip Sniper',
  tagline: 'Buys oversold bounces in an uptrend.',
  how: 'Waits for RSI to reset under a rising VWAP, then enters on reclaim.',
};

test('agent meta carries the agent name in title, og and twitter', () => {
  const m = seo.agentMeta(AGENT, ORIGIN, 'dip-sniper');
  assert.match(m, /<title>RUNECLAW — Dip Sniper<\/title>/);
  assert.match(m, /<meta property="og:title" content="RUNECLAW — Dip Sniper">/);
  assert.match(m, /<meta name="twitter:title" content="RUNECLAW — Dip Sniper">/);
  assert.match(m, /<meta name="twitter:card" content="summary_large_image">/);
});

test('agent meta canonical + og:url point at the per-agent URL', () => {
  const m = seo.agentMeta(AGENT, ORIGIN, 'dip-sniper');
  assert.match(m, /<link rel="canonical" href="https:\/\/example\.test\/agents\/dip-sniper">/);
  assert.match(m, /<meta property="og:url" content="https:\/\/example\.test\/agents\/dip-sniper">/);
});

test('agent meta emits valid JSON-LD naming the agent', () => {
  const m = seo.agentMeta(AGENT, ORIGIN, 'dip-sniper');
  const jsonld = m.match(/<script type="application\/ld\+json">(.*?)<\/script>/s);
  assert.ok(jsonld, 'expected a JSON-LD script');
  const data = JSON.parse(jsonld[1]);
  assert.equal(data['@type'], 'WebPage');
  assert.equal(data.name, 'RUNECLAW — Dip Sniper');
  assert.equal(data.url, 'https://example.test/agents/dip-sniper');
  assert.equal(data.isPartOf.name, 'RUNECLAW');
});

test('description is built from tagline + how, clamped, and never a dollar figure', () => {
  const m = seo.agentMeta(AGENT, ORIGIN, 'dip-sniper');
  const desc = m.match(/<meta name="description" content="([^"]*)">/)[1];
  assert.ok(desc.includes('oversold bounces'), 'uses the tagline');
  assert.ok(desc.length <= 201, 'description is clamped');
  assert.ok(!/\$\s*[0-9]|USD|\btoLocaleString\b/.test(m), 'no dollar figures anywhere in the meta');
});

test('a long tagline+how is clamped with an ellipsis', () => {
  const long = { id: 'x', name: 'X', tagline: 'word '.repeat(60), how: 'more '.repeat(60) };
  const desc = seo.agentMeta(long, ORIGIN, 'x').match(/<meta name="description" content="([^"]*)">/)[1];
  assert.ok(desc.length <= 201);
  assert.match(desc, /…$/);
});

test('injectAgentMeta replaces the marker with per-agent meta', () => {
  const html = '<head><meta charset="UTF-8">\n<!--AGENT_SEO-->\n</head>';
  const out = seo.injectAgentMeta(html, AGENT, ORIGIN, 'dip-sniper');
  assert.ok(!out.includes('<!--AGENT_SEO-->'), 'marker consumed');
  assert.match(out, /<title>RUNECLAW — Dip Sniper<\/title>/);
});

test('injectAgentMeta falls back to the generic directory card when agent is null', () => {
  const html = '<head><!--AGENT_SEO--></head>';
  const out = seo.injectAgentMeta(html, null, ORIGIN, 'unknown-slug');
  assert.match(out, /<title>RUNECLAW — Strategy Agents<\/title>/);
  assert.match(out, /<link rel="canonical" href="https:\/\/example\.test\/agents">/);
});

test('HTML metacharacters in agent fields are escaped (defense in depth)', () => {
  const evil = { id: 'e', name: 'A"><script>x</script>', tagline: 'x & y < z' };
  const m = seo.agentMeta(evil, ORIGIN, 'e');
  assert.ok(!/name="RUNECLAW — A"><script>/.test(m), 'name is attribute-escaped');
  assert.match(m, /&quot;|&lt;|&gt;|&amp;/);
  // JSON-LD must not contain a raw closing script tag
  assert.ok(!m.includes('</script>x'), 'no early script close from JSON-LD');
});

test('strategy.html carries the AGENT_SEO marker and no stale static title', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
  assert.ok(html.includes('<!--AGENT_SEO-->'), 'marker present for server injection');
  assert.ok(!/<title>RUNECLAW — Strategy Agent<\/title>/.test(html), 'the static per-page title was removed');
});

test('server.js wires the per-agent SEO route through lib/agent_seo', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(src, /require\('\.\/lib\/agent_seo'\)/);
  assert.match(src, /injectAgentMeta\(strategyHtml\(\)/);
  // the bare /agents route still precedes the parametrised one
  assert.ok(src.indexOf("app.get('/agents',") < src.indexOf("app.get('/agents/:slug'"),
    '/agents must be registered before /agents/:slug');
});
