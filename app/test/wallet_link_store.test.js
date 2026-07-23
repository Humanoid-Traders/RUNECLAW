'use strict';
/**
 * Durability of the phone/QR wallet-link store. The desktop mints a code + a
 * sign nonce; the phone redeems them — possibly after a redeploy or on a second
 * web instance that never saw the in-memory write. The store must serve those
 * from the DB when memory is cold, still enforce the TTL, and be single-use.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;                 // in-memory MockPool (with the new tables)

const test = require('node:test');
const assert = require('node:assert');
const store = require('../lib/wallet_link_store');

test('a link code survives a cold memory layer (served from the DB)', async () => {
  const code = 'a'.repeat(32);
  await store.putCode(code, 4242, Date.now() + 60_000);
  store._clearMemory();                            // simulate a restart / second instance
  const rec = await store.getCode(code);
  assert.ok(rec, 'code should rehydrate from the DB');
  assert.equal(String(rec.userId), '4242');
});

test('a sign nonce survives a cold memory layer and is case-normalised', async () => {
  const addr = '0xAbCdEf0000000000000000000000000000000001';
  await store.putNonce(addr, 'sign-me', Date.now() + 60_000);
  store._clearMemory();
  const rec = await store.getNonce(addr.toLowerCase());
  assert.ok(rec, 'nonce should rehydrate from the DB');
  assert.equal(rec.message, 'sign-me');
});

test('an expired code reads as null even though the row exists', async () => {
  const code = 'b'.repeat(32);
  await store.putCode(code, 7, Date.now() - 1000);  // already expired
  store._clearMemory();
  assert.equal(await store.getCode(code), null);
});

test('codes and nonces are single-use (delete removes from mem AND db)', async () => {
  const code = 'c'.repeat(32);
  await store.putCode(code, 9, Date.now() + 60_000);
  await store.delCode(code);
  store._clearMemory();
  assert.equal(await store.getCode(code), null);

  const addr = '0x' + '1'.repeat(40);
  await store.putNonce(addr, 'm', Date.now() + 60_000);
  await store.delNonce(addr);
  store._clearMemory();
  assert.equal(await store.getNonce(addr), null);
});

const fs = require('fs');
const path = require('path');

test('the QR is minted with a spec-compliant quiet zone (margin >= 4)', () => {
  const auth = fs.readFileSync(path.join(__dirname, '..', 'auth.js'), 'utf8');
  const m = auth.match(/toString\([^)]*margin:\s*(\d+)/);
  assert.ok(m, 'QR toString margin present');
  assert.ok(Number(m[1]) >= 4, 'margin must be >= 4 for reliable scanning, got ' + m[1]);
  // and auth.js routes codes/nonces through the durable store
  assert.match(auth, /require\('\.\/lib\/wallet_link_store'\)/);
  assert.match(auth, /_linkStore\.putCode|_linkStore\.getCode/);
});

test('the phone page offers multiple wallet deep links, not MetaMask only', () => {
  const wl = fs.readFileSync(path.join(__dirname, '..', 'public', 'wallet-link.html'), 'utf8');
  assert.match(wl, /metamask\.app\.link\/dapp\//);
  assert.match(wl, /link\.trustwallet\.com\/open_url/);
  assert.match(wl, /go\.cb-w\.com\/dapp/);
});
