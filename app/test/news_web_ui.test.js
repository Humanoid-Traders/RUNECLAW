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
