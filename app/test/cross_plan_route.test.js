'use strict';
/**
 * CROSS-2 guided yield execution — the admin preview proxy.
 *
 * Source-asserted: the route is authed + rate-limited, resolves the bot identity
 * server-side, relays the scanned move to the gateway's /cross/plan (read-only,
 * triple-gated bot-side), and never signs or broadcasts here — execution of the
 * first leg is a separate, gated /web3/sign call.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'web3_execute.js'), 'utf8');

test('the cross-plan preview is authed + rate-limited and identity-resolved', () => {
  // The router applies authMiddleware + rateLimit to every route in this file.
  assert.match(route, /router\.use\(authMiddleware\)/);
  assert.match(route, /router\.use\(rateLimit\(/);
  assert.match(route, /router\.post\('\/cross-plan'/);
  assert.match(route, /resolveBotIdentity\(req\)/);
});

test('it relays the move to the gateway /cross/plan', () => {
  assert.match(route, /postGateway\('\/cross\/plan'/);
  assert.match(route, /move: b\.move/);
  assert.match(route, /to_chain|dest/);
});

test('the cross-plan route never signs or broadcasts (preview only)', () => {
  // slice ONLY the cross-plan handler — stop before the /deploy JSDoc (which
  // legitimately documents signing/broadcast for that separate route).
  const fn = route.slice(route.indexOf("router.post('/cross-plan'"),
    route.indexOf('* POST /api/web3/deploy'));
  assert.ok(!/build_and_sign|sendRawTransaction|signTransaction|broadcast|private_key/.test(fn),
    'preview only — execution is a separate gated /web3/sign call');
});
