'use strict';
/**
 * Public, shareable Strategy-Agent profile page (/agents/:slug). Browser page +
 * a static route serving it. Source-asserted: the route serves strategy.html,
 * the page renders from the public catalogue, is §4-safe (percent/ratio only —
 * no dollar figure anywhere in its render code), and the marketplace links to
 * it. No auth, no money-path.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'strategy.html'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the /agents/:slug route server-renders the strategy page with per-agent SEO', () => {
  // The page is now injected with per-agent <head> meta via lib/agent_seo
  // (strategyHtml() reads public/strategy.html) rather than a bare sendFile.
  assert.match(server, /app\.get\('\/agents\/:slug'/);
  assert.match(server, /injectAgentMeta\(strategyHtml\(\)/);
  assert.match(server, /strategy\.html/);
  // distinct from the ERC-8004 identity card at /agent/:address
  assert.match(server, /app\.get\('\/agent\/:address'.*agent-card\.html/);
});

test('the page reads the public catalogue and matches by slug', () => {
  assert.match(html, /fetch\('\/api\/public\/strategies'/);
  assert.match(html, /location\.pathname\.split\('\/'\)\.filter\(Boolean\)/);
  assert.match(html, /String\(x\.id\)\.toLowerCase\(\) === slug/);
  // slug is validated to the catalogue slug shape before use
  assert.match(html, /\^\[a-z0-9\]\[a-z0-9-\]\{0,63\}\$/);
});

test('§4: the page renders percent/ratio only — no dollar figure', () => {
  // No dollar-amount rendering: no "$"-prefixed number, no '$' display literal,
  // no money formatter. (A regex end-anchor like {0,63}$ is fine.)
  assert.ok(!/\$\s*[0-9{]|'\$'|"\$"|fmtMoney|toLocaleString/.test(html),
    'strategy page must not render a dollar figure');
  // the scorecard tiles are the verified percent/ratio metrics
  assert.match(html, /total_return_pct/);
  assert.match(html, /Frozen backtest/);
});

test('the page is read-only and shareable (no trade path; Web Share)', () => {
  assert.ok(!/trade\/confirm|\/api\/trade|live_executor|api_key/.test(html), 'no money-path on the public page');
  assert.match(html, /navigator\.share/);
  assert.match(html, /Follow &amp; reproduce in the app/);
});

test('the marketplace cards link to each agent\'s public page', () => {
  assert.match(dash, /href="\/agents\/\$\{esc\(a\.id\)\}"/);
});

test('cache-buster bumped so the marketplace permalink ships', () => {
  const dhtml = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');
  assert.match(dhtml, /dashboard\.js\?v=(79|8\d)/);
});
