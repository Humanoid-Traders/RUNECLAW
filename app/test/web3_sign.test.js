'use strict';
/**
 * WEB3-LIVE-EXEC slice 2 — the /api/web3/sign relay (admin-only, testnet-only).
 *
 * The route is a thin admin-authed relay to the bot gateway, which owns the
 * signing key, the triple-gated default-OFF signing gate, the testnet-only
 * enforcement, and the envelope authorize(). Source-asserted: the route is
 * auth-gated, forwards the resolved identity + transfer params, and never
 * carries a private key or a broadcast flag over the wire.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'web3_execute.js'), 'utf8');

test('the /sign route is admin-authed and relays to the gateway', () => {
  assert.match(route, /authMiddleware/);
  assert.match(route, /router\.post\('\/sign'/);
  assert.match(route, /postGateway\('\/web3\/sign'/);
  assert.match(route, /resolveBotIdentity/);
});

test('the route never carries a key or a broadcast flag over the wire', () => {
  // the signing key lives bot-side and must never appear in the web relay.
  assert.ok(!/private_key|privateKey|WEB3_SIGNER_PRIVATE_KEY/.test(route), 'no key in the web layer');
  assert.ok(!/signTransaction|sendRawTransaction/.test(route), 'the web layer never signs');
  // broadcast is a bot-side, testnet-only decision — not a client-forwarded flag.
  assert.ok(!/broadcast\s*:/.test(route), 'no broadcast flag forwarded');
});

test('the sign relay forwards only the transfer parameters', () => {
  assert.match(route, /telegram_id: ident\.id/);
  assert.match(route, /value_wei:/);
  assert.match(route, /nonce:/);
  assert.match(route, /to: String\(b\.to/);
});
