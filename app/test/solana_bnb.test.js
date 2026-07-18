/**
 * PR NN — Solana + BNB Chain support.
 *
 * Pins: BNB Chain is a full member of the EVM chain registry (chainId 56,
 * curated majors, BNB priced via BNBUSDT); lib/solana.js validates base58
 * pubkeys, prices SOL + curated SPL majors off venue tickers, fails soft on
 * RPC errors, and exposes NO signing surface; the auth endpoints link/unlink
 * a watch address (bad addresses rejected); /api/wallet/portfolio merges the
 * Solana section for a user with only a watch address linked.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const jwt = require('jsonwebtoken');

const { pool } = require('../db');
const { CHAINS } = require('../lib/wallet');
const solana = require('../lib/solana');

// A real 32-byte base58 pubkey (the USDC mint) — valid shape for validation.
const GOOD_ADDR = 'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v';

let server, base, token, uid;

function request(method, path, { token, body } = {}) {
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

test.before(async () => {
  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['sol@test.io', 'x', 'S']);
  const [rows] = await pool.execute('SELECT id FROM users WHERE email = ?', ['sol@test.io']);
  uid = rows[0].id;
  token = jwt.sign({ user_id: uid, email: 'sol@test.io' }, process.env.JWT_SECRET);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/auth', require('../auth').router);
  app.use('/api/wallet', require('../routes/wallet'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => {
  if (server) server.close();
  solana.setRpcCall(null);
  solana.setTickerFetcher(null);
});

test('BNB Chain is a full EVM registry member', () => {
  const bnb = CHAINS.find(c => c.key === 'bnb');
  assert.ok(bnb, 'bnb chain missing from CHAINS');
  assert.strictEqual(bnb.chainId, 56);
  assert.strictEqual(bnb.native.symbol, 'BNB');
  assert.strictEqual(bnb.native.ticker, 'BNBUSDT');
  assert.strictEqual(bnb.rpcEnv, 'WEB3_RPC_URL_BNB');
  const syms = bnb.tokens.map(t => t.symbol);
  for (const s of ['USDT', 'USDC', 'ETH', 'BTCB', 'WBNB']) {
    assert.ok(syms.includes(s), `${s} missing from BNB tokens`);
  }
  // BSC-pegged tokens are all 18 decimals — a classic cross-chain footgun.
  for (const t of bnb.tokens) assert.strictEqual(t.decimals, 18, `${t.symbol} decimals`);
});

test('isSolanaAddress accepts a real pubkey and rejects junk', () => {
  assert.ok(solana.isSolanaAddress(GOOD_ADDR));
  assert.ok(!solana.isSolanaAddress('0x' + 'a'.repeat(40)));       // EVM address
  assert.ok(!solana.isSolanaAddress('IlO0' + 'a'.repeat(40)));     // non-base58 chars
  assert.ok(!solana.isSolanaAddress('abc'));                       // too short
  assert.ok(!solana.isSolanaAddress(''));
  assert.ok(!solana.isSolanaAddress(null));
});

test('getSolanaPortfolio prices SOL + curated SPL majors off tickers', async () => {
  solana.setTickerFetcher(async () => ({
    SOLUSDT: { price: 200 }, BONKUSDT: { price: 0.00002 },
  }));
  solana.setRpcCall(async (method) => {
    if (method === 'getBalance') return { value: 2_500_000_000 };  // 2.5 SOL
    if (method === 'getTokenAccountsByOwner') {
      const acct = (mint, uiAmount) => ({ account: { data: { parsed: { info: { mint, tokenAmount: { uiAmount } } } } } });
      return { value: [
        acct('EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', 100),      // USDC stable
        acct('DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263', 1_000_000), // BONK
        acct('SomeRandomUnknownMint1111111111111111111111', 999),        // ignored
      ] };
    }
    throw new Error('unexpected method ' + method);
  });
  const p = await solana.getSolanaPortfolio(GOOD_ADDR);
  assert.ok(p && p.read_only === true);
  const by = Object.fromEntries(p.assets.map(a => [a.symbol, a]));
  assert.strictEqual(by.SOL.amount, 2.5);
  assert.strictEqual(by.SOL.usd, 500);
  assert.strictEqual(by.USDC.usd, 100);          // stable pinned at $1
  assert.strictEqual(by.BONK.usd, 20);           // 1M * 0.00002
  assert.strictEqual(by.SomeRandom, undefined);  // unknown mint dropped
  assert.strictEqual(p.total_usd, 620);
  assert.strictEqual(p.chains[0].chain, 'solana');
});

test('an unreachable RPC degrades to an error section, never a throw', async () => {
  solana.setTickerFetcher(async () => ({}));
  solana.setRpcCall(async () => { throw new Error('rpc down'); });
  // Different address to dodge the read cache.
  const p = await solana.getSolanaPortfolio('Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB');
  assert.ok(p);
  assert.strictEqual(p.assets.length, 0);
  assert.strictEqual(p.error, 'rpc unreadable');
});

test('read-only invariant: no signing/sending surface is exported', () => {
  const banned = /sign|send|approve|transfer|withdraw|swap|stake/i;
  for (const name of Object.keys(solana)) {
    assert.ok(!banned.test(name), `export ${name} looks like a write surface`);
  }
});

test('watch-address link: valid stored, junk rejected, /me reflects it', async () => {
  let r = await request('POST', '/api/auth/wallet/solana', { token, body: { address: 'not-base58!' } });
  assert.strictEqual(r.status, 400);
  r = await request('POST', '/api/auth/wallet/solana', { token, body: { address: GOOD_ADDR } });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.sol_address, GOOD_ADDR);
  const me = await request('GET', '/api/auth/me', { token });
  assert.strictEqual(me.data.sol_address, GOOD_ADDR);
});

test('portfolio merges the Solana section for a watch-only user', async () => {
  solana.setTickerFetcher(async () => ({ SOLUSDT: { price: 100 } }));
  solana.setRpcCall(async (method) => {
    if (method === 'getBalance') return { value: 1_000_000_000 };  // 1 SOL
    return { value: [] };
  });
  const r = await request('GET', '/api/wallet/portfolio', { token });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.linked, true);
  assert.strictEqual(r.data.address, null);            // no EVM wallet linked
  assert.strictEqual(r.data.sol_address, GOOD_ADDR);
  const sol = r.data.chains.find(c => c.chain === 'solana');
  assert.ok(sol, 'solana section missing');
  assert.strictEqual(r.data.assets[0].symbol, 'SOL');

  // Unlink → the panel goes back to unlinked for a wallet-less user.
  const u = await request('POST', '/api/auth/wallet/solana/unlink', { token });
  assert.strictEqual(u.status, 200);
  const r2 = await request('GET', '/api/wallet/portfolio', { token });
  assert.strictEqual(r2.data.linked, false);
});
