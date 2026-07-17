/**
 * Unified cross-venue net worth — REST surface. JWT-authed; strictly the
 * caller's own holdings, aggregated read-only (lib/networth.js). The real
 * total never includes simulated paper equity.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { buildNetWorth } = require('../lib/networth');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 15, key: userKey }));

router.get('/', async (req, res) => {
  try {
    const ident = await resolveBotIdentity(req);
    res.json(await buildNetWorth(ident, req.user.user_id));
  } catch (err) {
    console.error('Net worth error:', err.message);
    res.status(500).json({ error: 'Net worth unavailable' });
  }
});

module.exports = router;
