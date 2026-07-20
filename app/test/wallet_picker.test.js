'use strict';
/**
 * Wallet picker (MH6) — Privy-style "Select your wallet" modal on EIP-6963
 * discovery, dependency-free. Browser-DOM code, so these are contract pins:
 * the discovery standard, the legacy fallback, the read-only honesty note,
 * and that every connect entry point routes through the picker instead of
 * grabbing whichever single wallet won the window.ethereum injection race.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const PUB = path.join(__dirname, '..', 'public');
const picker = fs.readFileSync(path.join(PUB, 'js', 'wallet_picker.js'), 'utf8');

test('picker implements EIP-6963 discovery with a legacy fallback', () => {
  assert.match(picker, /eip6963:announceProvider/);
  assert.match(picker, /eip6963:requestProvider/);
  assert.match(picker, /Browser wallet/, 'pre-6963 wallets still connectable');
  assert.match(picker, /Select your wallet/);
  assert.match(picker, /signs one login message — never a transaction/,
    'read-only honesty note inside the modal');
  assert.ok(picker.includes('data:image'), 'only data: URI icons rendered (CSP-safe)');
});

test('every connect entry point routes through the picker', () => {
  const index = fs.readFileSync(path.join(PUB, 'index.html'), 'utf8');
  assert.match(index, /js\/wallet_picker\.js/, 'landing loads the picker');
  const loginFn = index.slice(index.indexOf('async function doWalletLogin'),
    index.indexOf('async function doWalletLogin') + 400);
  assert.match(loginFn, /RCWalletPicker\.pick\(\)/);
  assert.ok(!loginFn.includes('window.ethereum'), 'login no longer races injection');
  const linkFn = index.slice(index.indexOf('async function linkWalletToAccount'),
    index.indexOf('async function linkWalletToAccount') + 400);
  assert.match(linkFn, /RCWalletPicker\.pick\(\)/);

  const dashHtml = fs.readFileSync(path.join(PUB, 'dashboard.html'), 'utf8');
  assert.match(dashHtml, /js\/wallet_picker\.js/, 'dashboard loads the picker');
  const dashJs = fs.readFileSync(path.join(PUB, 'js', 'dashboard.js'), 'utf8');
  assert.match(dashJs, /RCWalletPicker \? await RCWalletPicker\.pick\(\)/,
    'dashboard link handler prefers the picker');
});

test('no CSP loosening rode along — script sources stay pinned', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /"script-src 'self' 'unsafe-inline' https:\/\/telegram\.org"/,
    'script-src unchanged: the picker needs no external origins');
});
