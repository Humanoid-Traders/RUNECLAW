'use strict';
/**
 * wallet-link.html mobile fix: scanning the QR opens this page in the phone's
 * normal browser, which injects no signer. The page must (1) wait briefly for a
 * late-injected provider (in-app browsers inject after load) and (2) offer a
 * one-tap MetaMask deep link that reopens THIS page — code preserved — inside
 * MetaMask's own browser. Verified by source assertion (no headless wallet).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'wallet-link.html'), 'utf8');

test('the page waits for a late-injected provider before declaring no wallet', () => {
  assert.match(html, /function waitForEthereum/);
  assert.match(html, /setInterval/);
  // The success path only reveals the sign button once a provider is present.
  assert.match(html, /waitForEthereum\(function/);
});

test('no-wallet on mobile offers a MetaMask deep link that preserves the code', () => {
  // Universal link form: scheme dropped, host+path+query kept (so ?code= rides).
  assert.match(html, /metamask\.app\.link\/dapp\//);
  assert.match(html, /location\.host \+ location\.pathname \+ location\.search/);
  assert.match(html, /Open in MetaMask/);
  // Mobile detection gates the deep-link CTA.
  assert.match(html, /Android\|iPhone\|iPad\|iPod/);
});

test('other wallets and desktop still get honest guidance (no dead end)', () => {
  assert.match(html, /Trust Wallet/);
  assert.match(html, /Install a browser wallet/);
});
