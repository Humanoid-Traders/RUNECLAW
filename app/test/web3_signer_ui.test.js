'use strict';
/**
 * WEB3-LIVE-EXEC slice 2 — the signer web UI + deeper signing (nonce/gas prepare).
 *
 * The admin testnet signer, driven from the web/phone. Source-asserted: the
 * /sign/status + /sign/prepare relays are admin-authed and forward nothing but
 * the resolved identity + network (never a key or a broadcast flag), the /sign
 * relay forwards the prepared EIP-1559 fees, and the dashboard mounts a
 * mountTestnetSigner console that is admin-gated, never handles a private key,
 * derives wei without float error, and confirms before it broadcasts.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'web3_execute.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the status + prepare relays are admin-authed and relay to the gateway', () => {
  assert.match(route, /router\.get\('\/sign\/status'/);
  assert.match(route, /getGateway\(`\/web3\/sign\/status\?telegram_id=/);
  assert.match(route, /router\.post\('\/sign\/prepare'/);
  assert.match(route, /postGateway\('\/web3\/sign\/prepare'/);
  // both resolve the bot identity server-side (the router is authMiddleware-gated).
  assert.match(route, /authMiddleware/);
  assert.match(route, /resolveBotIdentity/);
});

test('no relay carries a key or a broadcast flag over the wire', () => {
  assert.ok(!/private_key|privateKey|WEB3_SIGNER_PRIVATE_KEY/.test(route), 'no key in the web layer');
  assert.ok(!/signTransaction|sendRawTransaction/.test(route), 'the web layer never signs');
  assert.ok(!/broadcast\s*:/.test(route), 'no broadcast flag forwarded');
});

test('the sign relay forwards the prepared EIP-1559 fees', () => {
  assert.match(route, /max_fee_wei: b\.max_fee_wei/);
  assert.match(route, /max_priority_wei: b\.max_priority_wei/);
});

test('the dashboard mounts an admin-gated testnet signer console', () => {
  assert.match(dash, /function mountTestnetSigner\(/);
  assert.match(html, /dashboard\.js\?v=5\d/);            // cache-buster bumped
  assert.match(dash, /id="p-signer"/);                    // panel present
  // mounted only inside the plan==='admin' branch (bot re-checks server-side).
  assert.match(dash, /plan === 'admin'[\s\S]*mountTestnetSigner/);
  // it drives the three signer endpoints.
  assert.match(dash, /\/api\/web3\/sign\/status/);
  assert.match(dash, /\/api\/web3\/sign\/prepare/);
  assert.match(dash, /'\/api\/web3\/sign'/);
});

test('the console never handles a private key and shows only the public address', () => {
  const fn = dash.slice(dash.indexOf('function mountTestnetSigner('));
  const body = fn.slice(0, fn.indexOf('\n  /* ═══════════════ Boot'));
  assert.ok(!/private_key|privateKey|WEB3_SIGNER_PRIVATE_KEY/.test(body), 'no key in the UI');
  assert.match(body, /signer_address/);                   // shows the public address
  assert.ok(!/signTransaction|raw_transaction/.test(body), 'the UI never signs');
});

test('a broadcast tx renders a clickable block-explorer link', () => {
  // the console prefers d.explorer_url (validated https) → a one-click on-chain
  // record; falls back to the bare hash otherwise.
  assert.match(dash, /d\.explorer_url && \/\^https:/);
  assert.match(dash, /href="\$\{esc\(d\.explorer_url\)\}" target="_blank" rel="noopener"/);
});

test('the signer address links to the selected network\'s explorer', () => {
  // per-testnet explorer hosts come from signer_status; the address link points
  // at the chosen chain's /address/ page and follows the network selector.
  assert.match(dash, /const addrLink = \(net\) =>/);
  assert.match(dash, /explorerByNet\[net\]/);
  assert.match(dash, /\/address\/\$\{a\}" target="_blank" rel="noopener"/);
  // validated https host only, and re-pointed when the network changes.
  assert.match(dash, /host && \/\^https:\\\/\\\/\/\.test\(host\)/);
  assert.match(dash, /id === 'sgn-net'[\s\S]*?addrLink\(e\.target\.value\)/);
});

test('wei is derived from ETH without float error, and a send confirms first', () => {
  // BigInt path — decimal ETH string → integer wei, never Number * 1e18.
  assert.match(dash, /weiFromEth/);
  assert.match(dash, /10n \*\* 18n/);
  // an explicit confirm() gate before a real testnet broadcast.
  assert.match(dash, /if \(!confirm\([^)]*TESTNET/);
  // prepare must precede sign — a stale/absent nonce is refused client-side too.
  assert.match(dash, /Run Prepare first/);
});
