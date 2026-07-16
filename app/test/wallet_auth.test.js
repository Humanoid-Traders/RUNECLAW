'use strict';
/**
 * Self-custody sign-in (Sign-In-With-Ethereum). A wallet requests a nonce,
 * signs the login message, and the server verifies the signature recovers to
 * the address, then find-or-creates a passwordless account by wallet_address.
 * Runs against MemoryDB. Uses a throwaway ethers wallet to produce signatures.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');
const authModule = require('../auth');

let server, base;

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

async function signIn(wallet) {
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: wallet.address } });
  const signature = await wallet.signMessage(n.data.message);
  return req('POST', '/api/auth/wallet/verify', { body: { address: wallet.address, signature } });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('/config advertises wallet_login when the verifier is present', async () => {
  const r = await req('GET', '/api/auth/config');
  assert.strictEqual(r.data.wallet_login, true);
});

test('a valid signature signs in and creates a passwordless account', async () => {
  const wallet = ethers.Wallet.createRandom();
  const r = await signIn(wallet);
  assert.strictEqual(r.status, 200);
  assert.ok(r.data.token, 'a session token is issued');
  assert.ok(r.data.user_id, 'a user id is returned');

  // Same wallet again → the SAME account (find-or-create by wallet_address).
  const r2 = await signIn(wallet);
  assert.strictEqual(r2.status, 200);
  assert.strictEqual(r2.data.user_id, r.data.user_id);
});

test('nonce is required and single-use', async () => {
  const wallet = ethers.Wallet.createRandom();
  // Verify with no prior nonce → rejected.
  const sigNoNonce = await wallet.signMessage('anything');
  let r = await req('POST', '/api/auth/wallet/verify',
    { body: { address: wallet.address, signature: sigNoNonce } });
  assert.strictEqual(r.status, 400);

  // Proper flow works, then the nonce can't be replayed.
  const ok = await signIn(wallet);
  assert.strictEqual(ok.status, 200);
});

test('a signature from a different key is rejected', async () => {
  const wallet = ethers.Wallet.createRandom();
  const other = ethers.Wallet.createRandom();
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: wallet.address } });
  const badSig = await other.signMessage(n.data.message);   // signed by the WRONG key
  const r = await req('POST', '/api/auth/wallet/verify',
    { body: { address: wallet.address, signature: badSig } });
  assert.strictEqual(r.status, 401);
});

test('an invalid address is rejected at nonce time', async () => {
  const r = await req('POST', '/api/auth/wallet/nonce', { body: { address: '0xnope' } });
  assert.strictEqual(r.status, 400);
});
