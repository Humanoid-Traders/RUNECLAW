/**
 * Web push subscriptions (JWT-authed).
 *
 * GET  /api/push/key          -> { enabled, public_key, subscribed }
 * POST /api/push/subscribe    -> store this browser's PushSubscription
 * POST /api/push/unsubscribe  -> remove it (by endpoint)
 *
 * Opt-in by construction: nothing is ever pushed to a user who hasn't
 * subscribed from their own browser, and unsubscribe is one call. Payloads
 * come only from the bot's already-sanitized public feed events — no
 * balances or per-user account data ride a push.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const push = require('../lib/push');

const router = express.Router();
router.use(authMiddleware);

const subLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

const MAX_SUBS_PER_USER = 5;   // one per browser/device is plenty
const MAX_ENDPOINT_LEN = 500;

router.get('/key', async (req, res) => {
  let subscribed = 0;
  try {
    const [rows] = await pool.execute(
      'SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?',
      [req.user.user_id]);
    subscribed = Number(rows[0]?.n || 0);
  } catch (e) { /* count is cosmetic */ }
  res.json({ enabled: push.isConfigured(), public_key: push.publicKey(), subscribed });
});

router.post('/subscribe', subLimit, async (req, res) => {
  try {
    if (!push.isConfigured()) {
      return res.status(503).json({ error: 'push_not_configured' });
    }
    const sub = (req.body || {}).subscription || {};
    const endpoint = String(sub.endpoint || '');
    const keys = sub.keys || {};
    if (!endpoint.startsWith('https://') || endpoint.length > MAX_ENDPOINT_LEN
        || !keys.p256dh || !keys.auth) {
      return res.status(400).json({ error: 'invalid subscription' });
    }
    const uid = req.user.user_id;
    const [existing] = await pool.execute(
      'SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id = ?', [uid]);
    if (Number(existing[0]?.n || 0) >= MAX_SUBS_PER_USER) {
      // Drop the oldest instead of refusing — re-subscribing must always work.
      await pool.execute(
        `DELETE FROM push_subscriptions WHERE user_id = ? ORDER BY id ASC LIMIT 1`, [uid]);
    }
    await pool.execute(
      `INSERT INTO push_subscriptions (user_id, endpoint, keys_json)
       VALUES (?, ?, ?)
       ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), keys_json = VALUES(keys_json)`,
      [uid, endpoint, JSON.stringify({ p256dh: String(keys.p256dh).slice(0, 200),
                                       auth: String(keys.auth).slice(0, 100) })]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Push subscribe error:', err.message);
    res.status(500).json({ error: 'Failed to subscribe' });
  }
});

router.post('/unsubscribe', subLimit, async (req, res) => {
  try {
    const endpoint = String((req.body || {}).endpoint || '');
    if (!endpoint) return res.status(400).json({ error: 'endpoint required' });
    // Scoped to the caller: nobody can unsubscribe someone else's browser.
    await pool.execute(
      'DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?',
      [req.user.user_id, endpoint]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Push unsubscribe error:', err.message);
    res.status(500).json({ error: 'Failed to unsubscribe' });
  }
});

module.exports = router;
