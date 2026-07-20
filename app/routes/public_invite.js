/**
 * PUBLIC invite recognition — personalizes the ?ref= landing, NO auth.
 *
 * GET /api/public/invite/:code -> { valid: true, handle: string|null }
 *
 * Reveals exactly ONE thing about the referrer, and only when they chose to
 * make it public themselves: their anonymous leaderboard handle (the same
 * name already shown on /leaderboard). No email, no user id, no join date —
 * a referral code must never become an account-enumeration oracle beyond
 * "this invite link works". Unknown codes 404. IP-rate-limited and briefly
 * cached per code.
 */

const express = require('express');
const { pool } = require('../db');
const { rateLimit, ipKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CODE_RE = /^[A-Za-z0-9_-]{4,32}$/;
const CACHE_MS = 60 * 1000;
const cache = new Map();       // code -> { at, status, data }

router.get('/:code', async (req, res) => {
  const code = String(req.params.code || '');
  if (!CODE_RE.test(code)) return res.status(400).json({ error: 'Invalid code' });
  const now = Date.now();
  const hit = cache.get(code);
  if (hit && (now - hit.at) < CACHE_MS) {
    return res.status(hit.status).json(hit.data);
  }
  try {
    const [rows] = await pool.execute(
      'SELECT id, leaderboard_handle FROM users WHERE referral_code = ?', [code]);
    const out = rows.length
      ? { status: 200, data: { valid: true, handle: rows[0].leaderboard_handle || null } }
      : { status: 404, data: { error: 'Unknown invite' } };
    if (cache.size > 128) cache.clear();
    cache.set(code, { at: now, ...out });
    res.status(out.status).json(out.data);
  } catch (err) {
    console.error('Public invite error:', err.message);
    res.status(500).json({ error: 'Invite lookup unavailable' });
  }
});

module.exports = router;
