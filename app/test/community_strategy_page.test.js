'use strict';
/**
 * Shareable community-strategy page. A published member strategy is reachable at
 * /agents/:slug (the same slug space as engine agents): the server resolves it
 * for per-strategy SEO, and strategy.html falls back to the community endpoint
 * and renders its rules (no scorecard). §4: the SEO card never claims a verified
 * backtest for a community config and carries no dollar figure.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const store = require('../lib/user_strategies');
const seo = require('../lib/agent_seo');

test('SEO meta for a community strategy uses its identity, no backtest claim, no $', async () => {
  const uid = 900;
  await store.create(uid, { name: 'Breakout Rider', how: 'ride momentum breakouts on trend days',
    regime: 'trend_up', rules: [{ type: 'direction', value: 'long_only' }, { type: 'min_rr', value: 2.5 }] });
  const mine = await store.listMine(uid);
  await store.setVisibility(uid, mine[0].dbId, 'public');
  const card = await store.getPublicBySlug(mine[0].slug);
  assert.ok(card && card.community);

  const meta = seo.agentMeta(card, 'https://runeclaw.example', card.slug);
  assert.match(meta, /RUNECLAW — Breakout Rider/);          // its own title
  assert.ok(!/verified, reproducible backtest/.test(meta), 'no engine-backtest claim for a community config');
  assert.ok(!meta.includes('$'), 'no dollar figure in the SEO card');
});

test('server resolves community slugs for /agents/:slug SEO (fallback wired)', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  // agentBySlug falls back to a published community strategy after the engine miss.
  assert.match(server, /getPublicBySlug\(slug\)/);
  assert.match(server, /require\('\.\/lib\/user_strategies'\)/);
});

test('strategy.html falls back to the community endpoint and renders rules, not a scorecard', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
  assert.match(html, /\/api\/public\/user-strategies\//);      // the fallback fetch
  assert.match(html, /function rulesBlock/);                   // renders rule chips
  assert.match(html, /a\.community \? rulesBlock\(a\.rules\) : scoreBlock/);
  // §4: the community disclaimer states config-only, no performance claim.
  assert.match(html, /member-authored <b>config<\/b>|community strategy is a member-authored/i);
});

test('community cards link to the shareable page', () => {
  const agents = fs.readFileSync(path.join(__dirname, '..', 'public', 'agents.html'), 'utf8');
  assert.match(agents, /class="ag-card ag-card--comm" href="\/agents\/'/);
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /href="\/agents\/\$\{encodeURIComponent\(a\.slug \|\| a\.id\)\}"/);
});
