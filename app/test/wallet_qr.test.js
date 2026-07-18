'use strict';
/**
 * QR phone linking: a single-use, account-bound code minted on desktop and
 * redeemed from the phone with a real SIWE-style signature. The code is the
 * bearer — no JWT ever travels to the phone.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { ethers } = require('ethers');
const { pool } = require('../db');
const authModule = require('../auth');
const { walletAddressOf } = require('../lib/wallet');

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

// Phone-side redemption with a real key.
async function redeem(code, signer) {
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: signer.address } });
  const signature = await signer.signMessage(n.data.message);
  return req('POST', '/api/auth/wallet/link-by-code', {
    body: { code, address: signer.address, signature },
  });
}

test('mint: authed gets code + URL + SVG QR; anonymous 401', async () => {
  const token = await newUser('qrmint');
  const r = await req('POST', '/api/auth/wallet/link-code', { token, body: {} });
  assert.equal(r.status, 200);
  assert.match(r.data.code, /^[0-9a-f]{32}$/);
  assert.match(r.data.url, /\/wallet-link\?code=[0-9a-f]{32}$/);
  assert.ok(String(r.data.svg || '').includes('<svg'), 'server-rendered QR SVG');
  assert.equal(r.data.expires_in_sec, 600);

  assert.equal((await req('POST', '/api/auth/wallet/link-code', { body: {} })).status, 401);
});

test('redeem: phone signature links the wallet to the MINTING account; code is single-use', async () => {
  const token = await newUser('qrlink');
  const mint = await req('POST', '/api/auth/wallet/link-code', { token, body: {} });
  const signer = ethers.Wallet.createRandom();

  const r = await redeem(mint.data.code, signer);
  assert.equal(r.status, 200);
  assert.equal(r.data.address, signer.address.toLowerCase());
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['qrlink@example.com']);
  assert.equal(await walletAddressOf(rows[0].id), signer.address.toLowerCase());

  // The code died with the first redemption.
  const again = await redeem(mint.data.code, ethers.Wallet.createRandom());
  assert.equal(again.status, 400);
  assert.match(again.data.error, /expired/);
});

test('redeem: unknown code 400; forged signature 401 (code survives the failure)', async () => {
  const token = await newUser('qrbad');
  const mint = await req('POST', '/api/auth/wallet/link-code', { token, body: {} });

  const bogus = await redeem('ab'.repeat(16), ethers.Wallet.createRandom());
  assert.equal(bogus.status, 400);

  // Wrong key: nonce for A, signed by B.
  const a = ethers.Wallet.createRandom(), b = ethers.Wallet.createRandom();
  const n = await req('POST', '/api/auth/wallet/nonce', { body: { address: a.address } });
  const signature = await b.signMessage(n.data.message);
  const forged = await req('POST', '/api/auth/wallet/link-by-code', {
    body: { code: mint.data.code, address: a.address, signature },
  });
  assert.equal(forged.status, 401);

  // A failed attempt must NOT consume the code — the real owner still links.
  const ok = await redeem(mint.data.code, a);
  assert.equal(ok.status, 200);
});

test('redeem: a wallet owned by another account is refused (409) and the code survives', async () => {
  const tokenOwner = await newUser('qrowner');
  const shared = ethers.Wallet.createRandom();
  const m1 = await req('POST', '/api/auth/wallet/link-code', { token: tokenOwner, body: {} });
  assert.equal((await redeem(m1.data.code, shared)).status, 200);

  const tokenThief = await newUser('qrthief');
  const m2 = await req('POST', '/api/auth/wallet/link-code', { token: tokenThief, body: {} });
  const r = await redeem(m2.data.code, shared);
  assert.equal(r.status, 409);
  // The thief's code still works for a wallet they actually own.
  const own = await redeem(m2.data.code, ethers.Wallet.createRandom());
  assert.equal(own.status, 200);
});
