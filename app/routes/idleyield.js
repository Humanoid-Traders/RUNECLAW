/**
 * Idle-Asset Yield Optimizer — REST surface. JWT-authed; the caller's own
 * wallet idle assets matched to the best cross-source rate (lib/idle_yield.js),
 * non-custodial preferred honestly. Read-only — recommendation, not execution.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { buildIdleYield } = require('../lib/idle_yield');

const router = express.Router();
router.use(authMiddleware);
// Each call reads the wallet across chains + hits the bot optimizer; keep it tight.
router.use(rateLimit({ windowMs: 60000, max: 8, key: userKey }));

router.get('/', async (req, res) => {
  try {
    const ident = await resolveBotIdentity(req);
    res.json(await buildIdleYield(ident, req.user.user_id));
  } catch (err) {
    console.error('Idle-yield error:', err.message);
    res.status(500).json({ error: 'Idle-yield unavailable' });
  }
});

module.exports = router;
