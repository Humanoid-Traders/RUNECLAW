/**
 * Cross-language envelope encryption for pending exchange credentials.
 *
 * Exchange API keys submitted on the website are encrypted AT REST here before
 * touching MySQL, and decrypted by the PYTHON bot when it pulls them (the bot
 * then re-stores them in its own Fernet store keyed by telegram_id and the
 * pending row is deleted). AES-256-GCM is used because both Node (crypto) and
 * Python (cryptography.AESGCM) implement it natively — no Fernet dep in Node.
 *
 * Key: WEB_CREDS_KEY, a base64 (standard or url-safe) 32-byte key shared by the
 * web app and the bot (same role as BOT_SYNC_SECRET). Encrypt output is a JSON
 * string { v, iv, tag, ct } with base64 fields; the matching Python decryptor
 * lives in the bot's credential puller.
 */

const crypto = require('crypto');

function loadKey() {
  const raw = process.env.WEB_CREDS_KEY || '';
  if (!raw) return null;
  // Accept standard or url-safe base64.
  const b64 = raw.replace(/-/g, '+').replace(/_/g, '/');
  let key;
  try { key = Buffer.from(b64, 'base64'); } catch (e) { return null; }
  if (key.length !== 32) return null;
  return key;
}

function isConfigured() {
  return loadKey() !== null;
}

/** Encrypt a JS object to a JSON envelope string. Throws if WEB_CREDS_KEY unusable. */
function encryptJSON(obj) {
  const key = loadKey();
  if (!key) throw new Error('WEB_CREDS_KEY missing or not a 32-byte base64 key');
  const iv = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
  const pt = Buffer.from(JSON.stringify(obj), 'utf8');
  const ct = Buffer.concat([cipher.update(pt), cipher.final()]);
  const tag = cipher.getAuthTag();
  return JSON.stringify({
    v: 1,
    iv: iv.toString('base64'),
    tag: tag.toString('base64'),
    ct: ct.toString('base64'),
  });
}

/** Decrypt a JSON envelope string back to the object (used by tests / Node-side). */
function decryptJSON(envelope) {
  const key = loadKey();
  if (!key) throw new Error('WEB_CREDS_KEY missing or not a 32-byte base64 key');
  const e = typeof envelope === 'string' ? JSON.parse(envelope) : envelope;
  const iv = Buffer.from(e.iv, 'base64');
  const tag = Buffer.from(e.tag, 'base64');
  const ct = Buffer.from(e.ct, 'base64');
  const decipher = crypto.createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  const pt = Buffer.concat([decipher.update(ct), decipher.final()]);
  return JSON.parse(pt.toString('utf8'));
}

module.exports = { isConfigured, encryptJSON, decryptJSON };
