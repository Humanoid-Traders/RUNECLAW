'use strict';
/**
 * Node-side reader for the operator secrets vault — so the WEBSITE survives a
 * wiped .env exactly like the bot does.
 *
 * The Python bot (bot/core/secrets_vault.py) mirrors money/auth-critical
 * secrets — including BOT_SYNC_SECRET and WEB_GATEWAY_SECRET — into an
 * encrypted vault under data/ that persists across redeploys, and self-heals
 * the bot's environment on boot. But the Express app is a SEPARATE process: it
 * never runs the Python config, so it never saw those restored secrets. Result:
 * a redeploy that wiped .env took the website down (FATAL: BOT_SYNC_SECRET must
 * be set) even though data/ still held the secret. This closes that gap.
 *
 * Format compatibility: the vault is a JSON map {KEY: fernet_token}. The master
 * key is a urlsafe-base64 32-byte Fernet key, read from RUNECLAW_SECRETS_KEY or
 * the persisted data/.exchange_secret.key file — identical to the Python side.
 * Fernet decryption is reimplemented on node:crypto (AES-128-CBC + HMAC-SHA256),
 * so there is NO new dependency.
 *
 * Safety: fail-open. Any missing file, bad key, tampered token, or crypto error
 * simply restores nothing — it never throws and never blocks boot. It only ever
 * FILLS IN secrets absent from the environment; a value already in process.env
 * always wins (the vault never overrides a live secret).
 */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const MASTER_KEY_BASENAME = '.exchange_secret.key';
const VAULT_BASENAME = 'secrets_vault.enc';

// The web secrets whose loss takes the site down or severs it from the bot.
// Mirrors the Python vault's web-pairing entries; extend via RUNECLAW_VAULT_KEYS.
const DEFAULT_WEB_KEYS = ['BOT_SYNC_SECRET', 'WEB_GATEWAY_SECRET', 'WEB_CREDS_KEY'];

/**
 * Candidate state directories, most-specific first. The bot writes the vault
 * relative to its own CWD (repo root); the web app usually runs from app/, so
 * we also look one level up. An explicit RUNECLAW_STATE_DIR always wins.
 */
function stateDirCandidates() {
  const out = [];
  const explicit = (process.env.RUNECLAW_STATE_DIR || '').trim();
  if (explicit) out.push(explicit);
  // app/ runs from the app dir; the vault lives at <repo>/data.
  out.push(path.join(__dirname, '..', '..', 'data'));
  out.push(path.join(process.cwd(), 'data'));
  out.push(path.join(process.cwd(), '..', 'data'));
  // De-dup while preserving order.
  return [...new Set(out)];
}

/** Read the raw 32-byte Fernet master key, or null if unavailable. */
function loadMasterKey(dir) {
  const envKey = (process.env.RUNECLAW_SECRETS_KEY || '').trim();
  const b64 = envKey || readKeyFile(dir);
  if (!b64) return null;
  let raw;
  try {
    raw = Buffer.from(b64, 'base64'); // base64url and standard both decode here
  } catch (_) {
    return null;
  }
  return raw.length === 32 ? raw : null;
}

function readKeyFile(dir) {
  try {
    return fs.readFileSync(path.join(dir, MASTER_KEY_BASENAME), 'utf8').trim();
  } catch (_) {
    return '';
  }
}

/**
 * Decrypt a single Fernet token. Returns the plaintext string, or null on any
 * verification/format/crypto failure. Fernet layout:
 *   0x80 | 8B timestamp | 16B IV | ciphertext(AES-128-CBC) | 32B HMAC-SHA256
 * key = 16B signing key || 16B encryption key.
 */
function fernetDecrypt(rawKey, token) {
  try {
    const signingKey = rawKey.subarray(0, 16);
    const encKey = rawKey.subarray(16, 32);
    const data = Buffer.from(String(token), 'base64'); // base64url-safe
    if (data.length < 1 + 8 + 16 + 32 || data[0] !== 0x80) return null;
    const mac = data.subarray(data.length - 32);
    const signed = data.subarray(0, data.length - 32);
    const expected = crypto.createHmac('sha256', signingKey).update(signed).digest();
    // Constant-time compare; length is fixed at 32 so this is safe.
    if (mac.length !== expected.length || !crypto.timingSafeEqual(mac, expected)) {
      return null;
    }
    const iv = data.subarray(9, 25);
    const ciphertext = data.subarray(25, data.length - 32);
    const decipher = crypto.createDecipheriv('aes-128-cbc', encKey, iv);
    const out = Buffer.concat([decipher.update(ciphertext), decipher.final()]);
    return out.toString('utf8');
  } catch (_) {
    return null; // bad padding, wrong key, tampered token — restore nothing
  }
}

function loadVault(dir) {
  try {
    const raw = fs.readFileSync(path.join(dir, VAULT_BASENAME), 'utf8');
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch (_) {
    return null;
  }
}

/**
 * Restore web secrets that are ABSENT from process.env from the shared vault.
 * Returns the array of key NAMES restored (never values). Fail-open: returns
 * [] on any problem. A key already present in the environment is never touched.
 *
 * @param {object} [opts]
 * @param {string[]} [opts.keys] override the managed key list (for tests)
 * @param {NodeJS.ProcessEnv} [opts.env] target env (defaults to process.env)
 * @param {string[]} [opts.dirs] state-dir candidates (for tests)
 */
function restoreFromVault(opts = {}) {
  const env = opts.env || process.env;
  const extra = (process.env.RUNECLAW_VAULT_KEYS || '')
    .split(',').map((s) => s.trim()).filter(Boolean);
  const keys = opts.keys || [...new Set([...DEFAULT_WEB_KEYS, ...extra])];
  const missing = keys.filter((k) => !(env[k] && String(env[k]).trim()));
  if (missing.length === 0) return []; // nothing to heal — the common case

  const dirs = opts.dirs || stateDirCandidates();
  for (const dir of dirs) {
    const vault = loadVault(dir);
    if (!vault) continue;
    const rawKey = loadMasterKey(dir);
    if (!rawKey) continue;
    const restored = [];
    for (const k of missing) {
      if (env[k] && String(env[k]).trim()) continue; // filled by an earlier dir
      const token = vault[k];
      if (!token) continue;
      const plain = fernetDecrypt(rawKey, token);
      if (plain && plain.trim()) {
        env[k] = plain;
        restored.push(k);
      }
    }
    if (restored.length) return restored; // first dir that yields secrets wins
  }
  return [];
}

module.exports = { restoreFromVault, fernetDecrypt, DEFAULT_WEB_KEYS };
