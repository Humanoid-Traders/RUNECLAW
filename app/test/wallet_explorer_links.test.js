'use strict';
/**
 * WEB3-ADDR2 — the Account wallet panel makes on-chain addresses clickable.
 *
 * Consistency with the testnet signer console (which links its address to a
 * block explorer): the SIWE-linked EVM wallet links to Etherscan and the Solana
 * watch address links to Solscan — read-only, one click to that address's
 * on-chain record. Source-asserted: both links are validated-shape,
 * rel=noopener, and fall back to the bare short address when malformed.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the linked EVM wallet address links to Etherscan, shape-guarded', () => {
  // only a well-formed 0x…40 address becomes a link; rel=noopener on the outbound.
  assert.match(dash, /\/\^0x\[0-9a-fA-F\]\{40\}\$\/\.test\(d\.address\)/);
  assert.match(dash, /href="https:\/\/etherscan\.io\/address\/\$\{esc\(d\.address\)\}" target="_blank" rel="noopener"/);
});

test('the Solana watch address links to Solscan, base58-guarded', () => {
  assert.match(dash, /\/\^\[1-9A-HJ-NP-Za-km-z\]\{32,44\}\$\/\.test\(d\.sol_address \|\| ''\)/);
  assert.match(dash, /href="https:\/\/solscan\.io\/account\/\$\{esc\(d\.sol_address\)\}" target="_blank" rel="noopener"/);
});

test('both links fall back to a bare short address when malformed', () => {
  // the ternary falses render a plain <b class="num"> — no dangling anchor.
  assert.match(dash, /: `<b class="num">\$\{esc\(short\)\}<\/b>`/);
  assert.match(dash, /: `<b class="num">\$\{esc\(solShort\)\}<\/b>`/);
});

test('the dashboard.js cache-buster is bumped', () => {
  assert.match(html, /dashboard\.js\?v=\d\d+/);
});
