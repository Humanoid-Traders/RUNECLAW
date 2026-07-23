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

test('no-wallet on mobile offers deep links that preserve the code', () => {
  // Universal link forms that reopen THIS page (code preserved) inside a wallet.
  assert.match(html, /metamask\.app\.link\/dapp\//);
  assert.match(html, /location\.host \+ location\.pathname \+ location\.search/);
  // Buttons are built as "Open in <wallet>" for each entry.
  assert.match(html, /'Open in ' \+ w\.name/);
  assert.match(html, /name: 'MetaMask'/);
  // Mobile detection gates the deep-link CTA.
  assert.match(html, /Android\|iPhone\|iPad\|iPod/);
});

test('multiple wallets (not MetaMask-only) and desktop guidance (no dead end)', () => {
  assert.match(html, /link\.trustwallet\.com\/open_url/);   // Trust Wallet
  assert.match(html, /go\.cb-w\.com\/dapp/);                // Coinbase Wallet
  assert.match(html, /Install a browser wallet/);           // desktop fallback
});
