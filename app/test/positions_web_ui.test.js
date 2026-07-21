/**
 * Live positions & stop-loss truth — the web surface: a JWT-authed proxy route
 * to the bot gateway plus a Portfolio-view panel. The route needs a live
 * gateway and the panel is DOM code with no headless harness, so both are
 * verified by source assertion (same approach as news_web_ui.test.js).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'positions.js'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');

test('the positions route is JWT-authed and proxies the bot gateway', () => {
  assert.match(route, /authMiddleware/);
  assert.match(route, /getGateway\(`\/positions\?telegram_id=/);
  assert.match(route, /isConfigured\(\)/);   // 503 when the bridge is off
});

test('the positions route is mounted', () => {
  assert.match(server, /app\.use\('\/api\/positions', require\('\.\/routes\/positions'\)\)/);
});

test('the Portfolio view has a positions & stop-loss panel wired in', () => {
  assert.match(dash, /id="p-lpos"/);
  assert.match(dash, /renderPanel\(C\('lpos'\)/);
  assert.match(dash, /fetchJSON\('\/api\/positions'/);
});

test('the panel surfaces protection truth: on-exchange, bot-managed, unprotected', () => {
  assert.match(dash, /on exchange/);
  assert.match(dash, /bot-managed/);
  assert.match(dash, /unprotected/);
  assert.match(dash, /p\.sl_order === 'exchange'/);
});
