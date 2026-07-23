'use strict';
/**
 * Durable store for the phone/QR wallet-link flow: the desktop mints a single-
 * use link CODE and a per-address sign NONCE; the phone redeems them — possibly
 * on a different web instance, or after a redeploy. Holding those only in process
 * memory means a restart between "show QR" and "phone signs" fails with
 * "code/nonce expired." This persists them to the DB while keeping a fast
 * in-memory hit path, and falls back to memory alone if the DB is unavailable —
 * so a DB hiccup degrades to the old behaviour rather than breaking linking.
 *
 * Security invariants are unchanged from the in-memory version: TTL is enforced
 * on read, entries are single-use (deleted after redemption), and the caller
 * still verifies the signature. This layer only decides WHERE the short-lived
 * record lives.
 */

const _memCodes = new Map();    // code -> { userId, expires }
const _memNonces = new Map();   // address(lower) -> { message, expires }

function _pool() { try { return require('../db').pool; } catch (_) { return null; } }
const _now = () => Date.now();

// Opportunistic prune so expired rows don't accumulate (best-effort, ignored on
// failure). Runs on writes, which are infrequent (one per link attempt).
async function _prune(table) {
  try { await _pool().execute(`DELETE FROM ${table} WHERE expires_at < ?`, [_now()]); } catch (_) { /* best-effort */ }
}

// ---- link codes -----------------------------------------------------------
async function putCode(code, userId, expires) {
  _memCodes.set(code, { userId, expires });
  if (_memCodes.size > 5000) _memCodes.clear();
  try { await _pool().execute(
    'REPLACE INTO wallet_link_codes (code, user_id, expires_at) VALUES (?, ?, ?)',
    [code, String(userId), expires]); } catch (_) { /* mem still holds it */ }
  _prune('wallet_link_codes');
}

async function getCode(code) {
  const m = _memCodes.get(code);
  if (m) return m.expires < _now() ? null : m;
  try {
    const [rows] = await _pool().execute(
      'SELECT user_id, expires_at FROM wallet_link_codes WHERE code = ? LIMIT 1', [code]);
    if (rows && rows.length) {
      const rec = { userId: rows[0].user_id, expires: Number(rows[0].expires_at) };
      return rec.expires < _now() ? null : rec;
    }
  } catch (_) { /* fall through */ }
  return null;
}

async function delCode(code) {
  _memCodes.delete(code);
  try { await _pool().execute('DELETE FROM wallet_link_codes WHERE code = ?', [code]); } catch (_) { /* best-effort */ }
}

// ---- sign nonces ----------------------------------------------------------
async function putNonce(address, message, expires) {
  const a = String(address).toLowerCase();
  _memNonces.set(a, { message, expires });
  if (_memNonces.size > 5000) _memNonces.clear();
  try { await _pool().execute(
    'REPLACE INTO wallet_link_nonces (address, message, expires_at) VALUES (?, ?, ?)',
    [a, message, expires]); } catch (_) { /* mem still holds it */ }
  _prune('wallet_link_nonces');
}

async function getNonce(address) {
  const a = String(address).toLowerCase();
  const m = _memNonces.get(a);
  if (m) return m.expires < _now() ? null : m;
  try {
    const [rows] = await _pool().execute(
      'SELECT message, expires_at FROM wallet_link_nonces WHERE address = ? LIMIT 1', [a]);
    if (rows && rows.length) {
      const rec = { message: rows[0].message, expires: Number(rows[0].expires_at) };
      return rec.expires < _now() ? null : rec;
    }
  } catch (_) { /* fall through */ }
  return null;
}

async function delNonce(address) {
  const a = String(address).toLowerCase();
  _memNonces.delete(a);
  try { await _pool().execute('DELETE FROM wallet_link_nonces WHERE address = ?', [a]); } catch (_) { /* best-effort */ }
}

// Test-only: drop the in-memory layer to exercise the DB round trip (simulates a
// redeploy / a second web instance that never saw the write).
function _clearMemory() { _memCodes.clear(); _memNonces.clear(); }

module.exports = { putCode, getCode, delCode, putNonce, getNonce, delNonce, _clearMemory };
