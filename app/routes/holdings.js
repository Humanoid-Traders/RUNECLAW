/**
 * Funds by venue & wallet — REST surface. JWT-authed; strictly the caller's
 * own balances, itemised read-only per source (lib/holdings.js). Paper equity
 * is not part of this view; the real total counts only readable real money.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { buildHoldings } = require('../lib/holdings');

const router = express.Router();
router.use(authMiddleware);
// Tight cap: each call fans out to a per-venue bot-gateway probe plus wallet
// RPC reads across every chain — the heaviest read on the site.
router.use(rateLimit({ windowMs: 60000, max: 6, key: userKey }));

router.get('/', async (req, res) => {
  try {
    const ident = await resolveBotIdentity(req);
    res.json(await buildHoldings(ident, req.user.user_id));
  } catch (err) {
    console.error('Holdings error:', err.message);
    res.status(500).json({ error: 'Holdings unavailable' });
  }
});

module.exports = router;
