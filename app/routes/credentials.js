/**
 * Exchange-credential management (user-facing, JWT-authed).
 *
 * The user submits Bitget API keys here. They are encrypted at rest immediately
 * (AES-256-GCM, WEB_CREDS_KEY) into a short-lived `pending_credentials` row; the
 * bot PULLS pending rows over the shared-secret channel, imports them into its
 * own Fernet store keyed by telegram_id, and the row is deleted. Raw keys are
 * NEVER stored in plaintext and NEVER logged.
 *
 * Prerequisite: the account must have linked Telegram (so we know which bot
 * account the keys belong to). Keys should be withdrawal-disabled on Bitget.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const creds = require('../lib/creds_crypto');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(authMiddleware);

// Per-user limit on the mutating credential endpoints (submit/disconnect). GET
// /status is left unlimited (cheap read). Money endpoint — keep it tight.
const credLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

// Max accepted length for any single key field (a real Bitget key is ~64 chars;
// this bounds a malicious oversized payload before it is encrypted/stored).
const MAX_FIELD = 512;

// Security audit line (never logs key material).
function secLog(event, req, extra) {
  const uid = req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
}

async function _userRow(uid) {
  const [rows] = await pool.execute(
    'SELECT telegram_linked, telegram_id FROM users WHERE id = ?', [uid]);
  return rows[0] || null;
}

// GET /api/credentials/status -> { linked, connected, pending }
router.get('/status', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    const [st] = await pool.execute(
      'SELECT connected FROM exchange_status WHERE user_id = ?', [uid]);
    const [pend] = await pool.execute(
      'SELECT action FROM pending_credentials WHERE user_id = ?', [uid]);
    res.json({
      linked: !!(u && u.telegram_linked),
      connected: st.length > 0 ? !!st[0].connected : false,
      pending: pend.length > 0 ? pend[0].action : null,
      crypto_ready: creds.isConfigured(),
    });
  } catch (err) {
    console.error('Cred status error:', err.message);
    res.status(500).json({ error: 'Failed to read status' });
  }
});

// POST /api/credentials  body: { api_key, api_secret, passphrase }
router.post('/', credLimit, async (req, res) => {
  try {
    if (!creds.isConfigured()) {
      return res.status(503).json({ error: 'Credential encryption not configured (WEB_CREDS_KEY)' });
    }
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    if (!u || !u.telegram_linked || !u.telegram_id) {
      return res.status(409).json({ error: 'Link your Telegram first, then connect your exchange.' });
    }
    const { api_key, api_secret, passphrase } = req.body || {};
    if (!api_key || !api_secret || !passphrase) {
      return res.status(400).json({ error: 'api_key, api_secret and passphrase are required.' });
    }
    if ([api_key, api_secret, passphrase].some(v => typeof v !== 'string' || v.length > MAX_FIELD)) {
      return res.status(400).json({ error: 'Credential fields are malformed or too long.' });
    }
    // Encrypt the secret material at rest immediately. Never logged.
    const payload = creds.encryptJSON({
      api_key: String(api_key), api_secret: String(api_secret),
      passphrase: String(passphrase),
    });
    await pool.execute(
      `INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
       VALUES (?, ?, 'bitget', 'connect', ?)
       ON DUPLICATE KEY UPDATE telegram_id = VALUES(telegram_id),
         action = 'connect', encrypted_payload = VALUES(encrypted_payload),
         created_at = CURRENT_TIMESTAMP`,
      [uid, String(u.telegram_id), payload]
    );
    secLog('exchange_connect_submitted', req);
    res.json({ ok: true, pending: 'connect' });
  } catch (err) {
    console.error('Cred submit error:', err.message); // never logs the body
    res.status(500).json({ error: 'Failed to submit credentials' });
  }
});

// DELETE /api/credentials -> queue a disconnect (bot removes them from its store)
router.delete('/', credLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const u = await _userRow(uid);
    const tg = u && u.telegram_id ? String(u.telegram_id) : '';
    await pool.execute(
      `INSERT INTO pending_credentials (user_id, telegram_id, exchange, action, encrypted_payload)
       VALUES (?, ?, 'bitget', 'disconnect', NULL)
       ON DUPLICATE KEY UPDATE action = 'disconnect', encrypted_payload = NULL,
         created_at = CURRENT_TIMESTAMP`,
      [uid, tg]
    );
    secLog('exchange_disconnect_requested', req);
    res.json({ ok: true, pending: 'disconnect' });
  } catch (err) {
    console.error('Cred disconnect error:', err.message);
    res.status(500).json({ error: 'Failed to queue disconnect' });
  }
});

module.exports = router;
