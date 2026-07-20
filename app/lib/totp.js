'use strict';
/**
 * TOTP two-factor auth (RFC 6238 / RFC 4226) — dependency-free.
 *
 * Standard authenticator-app 2FA: HMAC-SHA1, 6 digits, 30s period, ±1 step
 * of clock drift accepted. Secrets are 20 random bytes (base32 on the wire,
 * the format every authenticator app expects). Backup codes are one-time:
 * only their SHA-256 hashes are stored, and a used code is removed.
 *
 * SHA-1 here is the RFC-mandated HMAC primitive for interop with Google
 * Authenticator/Aegis/1Password etc. — not a general-purpose hash choice.
 */

const crypto = require('crypto');

const B32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function base32Encode(buf) {
  let bits = 0, value = 0, out = '';
  for (const byte of buf) {
    value = (value << 8) | byte;
    bits += 8;
    while (bits >= 5) {
      out += B32_ALPHABET[(value >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits > 0) out += B32_ALPHABET[(value << (5 - bits)) & 31];
  return out;
}

function base32Decode(str) {
  const clean = String(str || '').toUpperCase().replace(/[^A-Z2-7]/g, '');
  let bits = 0, value = 0;
  const out = [];
  for (const ch of clean) {
    value = (value << 5) | B32_ALPHABET.indexOf(ch);
    bits += 5;
    if (bits >= 8) {
      out.push((value >>> (bits - 8)) & 0xff);
      bits -= 8;
    }
  }
  return Buffer.from(out);
}

function generateSecret() {
  return base32Encode(crypto.randomBytes(20));
}

/** RFC 4226 HOTP: HMAC-SHA1 + dynamic truncation, 6 digits. */
function hotp(secretB32, counter) {
  const key = base32Decode(secretB32);
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const mac = crypto.createHmac('sha1', key).update(msg).digest();
  const offset = mac[mac.length - 1] & 0x0f;
  const code = ((mac[offset] & 0x7f) << 24)
    | (mac[offset + 1] << 16) | (mac[offset + 2] << 8) | mac[offset + 3];
  return String(code % 1_000_000).padStart(6, '0');
}

/** Verify a 6-digit code against now ±1 period (RFC 6238, 30s steps). */
function verifyTotp(secretB32, code, nowMs) {
  const c = String(code || '').replace(/\s+/g, '');
  if (!/^\d{6}$/.test(c) || !secretB32) return false;
  const counter = Math.floor((nowMs ?? Date.now()) / 30_000);
  for (const step of [0, -1, 1]) {
    const expected = hotp(secretB32, counter + step);
    if (crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(c))) return true;
  }
  return false;
}

function otpauthUri(secretB32, email) {
  const label = encodeURIComponent(`RUNECLAW:${email || 'account'}`);
  return `otpauth://totp/${label}?secret=${secretB32}&issuer=RUNECLAW&algorithm=SHA1&digits=6&period=30`;
}

// ── One-time backup codes ────────────────────────────────────────────────────

function hashBackupCode(code) {
  return crypto.createHash('sha256')
    .update(String(code || '').toUpperCase().replace(/[^A-Z0-9]/g, ''))
    .digest('hex');
}

/** 8 codes like "7Q2M-KX9D". Return {codes (show ONCE), hashes (store)}. */
function generateBackupCodes(n = 8) {
  const codes = [];
  for (let i = 0; i < n; i++) {
    const raw = base32Encode(crypto.randomBytes(5)).slice(0, 8);
    codes.push(`${raw.slice(0, 4)}-${raw.slice(4)}`);
  }
  return { codes, hashes: codes.map(hashBackupCode) };
}

/** If `code` matches a stored hash, return the remaining hashes; else null. */
function consumeBackupCode(code, hashes) {
  const h = hashBackupCode(code);
  const list = Array.isArray(hashes) ? hashes : [];
  const idx = list.indexOf(h);
  if (idx === -1) return null;
  return list.slice(0, idx).concat(list.slice(idx + 1));
}

module.exports = {
  generateSecret, hotp, verifyTotp, otpauthUri,
  generateBackupCodes, consumeBackupCode, hashBackupCode,
  base32Encode, base32Decode,
};
