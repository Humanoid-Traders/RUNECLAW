/**
 * Open positions + stop-loss PROTECTION TRUTH — REST surface. JWT-authed;
 * read-only mirror of Telegram /open_positions. Each position reports whether
 * its stop-loss is actually live ON THE EXCHANGE (protected) or bot-managed,
 * and flags any LIVE position missing its exchange stop as unprotected. It
 * only reads — it never places, moves, sizes, or closes an order.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/positions?telegram_id=${encodeURIComponent(ident.id)}`, 15000));
  } catch (err) {
    console.error('Positions error:', err.message);
    res.status(502).json({ error: 'Positions unavailable' });
  }
});

module.exports = router;
