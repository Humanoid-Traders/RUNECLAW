'use strict';
/**
 * Contract Studio route — the authed proxy to the bot's Solidity drafter.
 *
 * Source-asserted: the route is JWT-authed + rate-limited, resolves the bot
 * identity server-side (a browser can never draft as someone else), relays to
 * the gateway's /contract/studio, validates the compiler hints, and is mounted
 * in server.js. No money-path — it proxies text generation only.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'contract.js'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');

test('the studio route is authed, rate-limited and identity-resolved', () => {
  assert.match(route, /authMiddleware/);
  assert.match(route, /router\.use\(authMiddleware\)/);
  assert.match(route, /rateLimit\(/);
  assert.match(route, /resolveBotIdentity\(req\)/);
});

test('it relays to the gateway /contract/studio with a spec', () => {
  assert.match(route, /router\.post\('\/studio'/);
  assert.match(route, /postGateway\('\/contract\/studio'/);
  assert.match(route, /spec/);
  // compiler hints are charset-bounded, never arbitrary text.
  assert.match(route, /license|pragma/);
});

test('the studio route never signs, deploys or moves value', () => {
  assert.ok(!/signTransaction|sendRawTransaction|private_key|broadcast/.test(route),
    'text generation only — no money-path in the studio route');
});

test('the compile route proxies to the gateway and is authed + rate-limited', () => {
  assert.match(route, /router\.post\('\/compile'/);
  assert.match(route, /postGateway\('\/contract\/compile'/);
  assert.match(route, /compileLimit/);
  // it forwards the source and a size bound, never signs anything.
  assert.match(route, /solidity/);
  assert.match(route, /MAX_SOURCE_LEN/);
  const compileFn = route.slice(route.indexOf("router.post('/compile'"));
  assert.ok(!/signTransaction|sendRawTransaction|private_key|broadcast/.test(compileFn),
    'compile is pure computation — no money-path in the compile route');
});

test('the route is mounted in server.js', () => {
  assert.match(server, /app\.use\('\/api\/contract', require\('\.\/routes\/contract'\)\)/);
});
