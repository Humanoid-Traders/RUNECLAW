/**
 * Risk sentry — REST surface. JWT-authed; a proactive read-only watch over the
 * caller's own standing book (envelope drift, over-cap, concentration,
 * crowding, daily-spend). Detection-only — it flags, it never acts.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 12, key: userKey }));

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/sentry?telegram_id=${encodeURIComponent(ident.id)}`, 15000));
  } catch (err) {
    console.error('Risk sentry error:', err.message);
    res.status(502).json({ error: 'Risk sentry unavailable' });
  }
});

module.exports = router;
