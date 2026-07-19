/**
 * Proof-of-PnL — REST surface for the continuously-published, verifiable
 * track-record statement. JWT-authed; serves the latest public-safe bundle with
 * its freshness + re-derived integrity + the anchor's honest UNVERIFIED status.
 * "Don't trust the dashboard — verify the fills."
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/proofofpnl?telegram_id=${encodeURIComponent(ident.id)}`, 15000));
  } catch (err) {
    console.error('Proof-of-PnL error:', err.message);
    res.status(502).json({ error: 'Proof-of-PnL unavailable' });
  }
});

module.exports = router;
