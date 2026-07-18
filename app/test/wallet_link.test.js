'use strict';
/**
 * Wallet LINK for existing accounts: users who signed up with email had no
 * way to attach a wallet (the landing "Continue with a wallet" is a login
 * method). Covers the SIWE-style proof, ownership conflicts, unlink, and
 * that the linked address feeds the wallet mirror lookup.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');
const authModule = require('../auth');
const wallet = require('../lib/wallet');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

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

async function newUser(tag) {
  const r = await req('POST', '/api/auth/register', {
    body: { email: `${tag}@example.com`, password: 'longenough1' },
  });
  return r.data.token;
}

// Full SIWE-style link: nonce → sign with a REAL key → link.
async function linkWallet(token, signer) {
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: signer.address } });
  assert.equal(n.status, 200, 'nonce issued');
  const signature = await signer.signMessage(n.data.message);
  return req('POST', '/api/auth/wallet/link', {
    token, body: { address: signer.address, signature },
  });
}

test('link: email user attaches a wallet with a valid signature', async () => {
  const token = await newUser('linker');
  const signer = ethers.Wallet.createRandom();
  const r = await linkWallet(token, signer);
  assert.equal(r.status, 200);
  assert.equal(r.data.address, signer.address.toLowerCase());
  // The linked address now feeds the read-only mirror lookup.
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['linker@example.com']);
  assert.equal(await wallet.walletAddressOf(rows[0].id), signer.address.toLowerCase());
});

test('link: a wallet already owned by another account is refused (409)', async () => {
  const tokenA = await newUser('ownera');
  const tokenB = await newUser('ownerb');
  const signer = ethers.Wallet.createRandom();
  assert.equal((await linkWallet(tokenA, signer)).status, 200);
  const r = await linkWallet(tokenB, signer);
  assert.equal(r.status, 409);
  assert.match(r.data.error, /another account/);
});

test('link: a bad signature never links', async () => {
  const token = await newUser('badsig');
  const signer = ethers.Wallet.createRandom();
  const other = ethers.Wallet.createRandom();
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: signer.address } });
  const signature = await other.signMessage(n.data.message);   // wrong key
  const r = await req('POST', '/api/auth/wallet/link', {
    token, body: { address: signer.address, signature },
  });
  assert.equal(r.status, 401);
});

test('link requires auth; unlink clears the address', async () => {
  const anon = await req('POST', '/api/auth/wallet/link', {
    body: { address: '0x' + 'ab'.repeat(20), signature: '0xdead' },
  });
  assert.equal(anon.status, 401);

  const token = await newUser('unlinker');
  const signer = ethers.Wallet.createRandom();
  assert.equal((await linkWallet(token, signer)).status, 200);
  const u = await req('POST', '/api/auth/wallet/unlink', { token, body: {} });
  assert.equal(u.status, 200);
  const { pool } = require('../db');
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['unlinker@example.com']);
  assert.equal(await wallet.walletAddressOf(rows[0].id), null);
});
