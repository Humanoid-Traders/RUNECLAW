/**
 * NEWS-1c: the web news surface — a JWT-authed proxy route to the bot gateway
 * plus a dashboard "News" view. Verified by source assertion (the route needs a
 * live gateway; the view is DOM code with no headless harness).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'news.js'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the news route is JWT-authed and proxies the bot gateway', () => {
  assert.match(route, /authMiddleware/);
  assert.match(route, /getGateway\(`\/news\?telegram_id=/);
  assert.match(route, /isConfigured\(\)/);   // 503 when the bridge is off
});

test('the news route is mounted', () => {
  assert.match(server, /app\.use\('\/api\/news', require\('\.\/routes\/news'\)\)/);
});

test('the dashboard has a News view wired into nav + router', () => {
  assert.match(dash, /\{ id: 'news',\s*label: 'News'/);
  assert.match(dash, /news: renderNews/);
  assert.match(dash, /async function renderNews\(\)/);
});

test('the News view is advisory and surfaces held-position alerts', () => {
  assert.match(dash, /fetchJSON\('\/api\/news'\)/);
  assert.match(dash, /data\.standdown/);
  assert.match(dash, /never moves or blocks a trade/);
  assert.match(dash, /NEWS_RADAR_ENABLED=0/);   // off-state guidance (on by default)
});

// ── NEWS-2: bring-your-own paid news key ──

test('the BYON key routes proxy the gateway and never store the key here', () => {
  assert.match(route, /router\.post\('\/key'/);
  assert.match(route, /router\.post\('\/key\/clear'/);
  assert.match(route, /router\.get\('\/key\/status'/);
  assert.match(route, /postGateway\('\/news\/key'/);
  assert.match(route, /getGateway\(\s*`\/news\/key\/status\?telegram_id=/);
  // key submissions are rate-limited and length-bounded before proxying
  assert.match(route, /keyWriteLimit/);
  assert.match(route, /NEWS_MAX_KEY_LEN/);
});

test('the News view has a BYON key panel that only shows a fingerprint back', () => {
  assert.match(dash, /function drawNewsKey\(/);
  assert.match(dash, /fetchJSON\('\/api\/news\/key\/status'/);
  assert.match(dash, /fetchJSON\('\/api\/news\/key',\s*\{\s*method:\s*'POST'/);
  assert.match(dash, /fetchJSON\('\/api\/news\/key\/clear'/);
  assert.match(dash, /d\.fingerprint/);        // status shows a masked fingerprint
  assert.match(dash, /type="password"/);       // the key input is masked
});

test('BYON-enriched items are provenance-tagged and body-free in the feed', () => {
  assert.match(dash, /data\.byon/);
  assert.match(dash, />yours</);               // provenance chip on the user's items
  // compliance copy: headlines/source/link only, never paywalled article text
  assert.match(dash, /never paywalled article text/);
});

test('the share-panel remove/clear listener is bound once (no leak)', () => {
  // renderPanel only swaps innerHTML on the persistent container, so the
  // delegated click handler must be guarded — re-binding every drawShare()
  // would stack listeners and multi-fire each Remove. (Regression guard.)
  assert.match(dash, /wrap\._shareBound/);
  assert.match(dash, /if \(wrap && !wrap\._shareBound\)/);
  // the fragile float layout was replaced with a flex header
  assert.ok(!/data-del="\$\{esc\(String\(n\.id\)\)\}" type="button" style="float:right"/.test(dash));
});

test('the BYON key gateway routes are registered bot-side', () => {
  const gw = fs.readFileSync(
    path.join(__dirname, '..', '..', 'bot', 'web', 'user_gateway.py'), 'utf8');
  assert.match(gw, /add_post\("\/news\/key",\s*handle_news_key_save\)/);
  assert.match(gw, /add_post\("\/news\/key\/clear",\s*handle_news_key_clear\)/);
  assert.match(gw, /add_get\("\/news\/key\/status",\s*handle_news_key_status\)/);
});
