/**
 * News radar — REST surface (NEWS-1c). JWT-authed; a read-only public-RSS
 * headline feed with high-impact flags on the caller's held positions. Advisory
 * only — it flags, it never trades, sizes, or blocks anything.
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
    relay(res, await getGateway(`/news?telegram_id=${encodeURIComponent(ident.id)}`, 15000));
  } catch (err) {
    console.error('News radar error:', err.message);
    res.status(502).json({ error: 'News radar unavailable' });
  }
});

module.exports = router;
