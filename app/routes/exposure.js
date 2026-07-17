/**
 * Cross-venue exposure — REST surface. JWT-authed, strictly the caller's own
 * open positions + wallet, read-only (lib/exposure.js).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { buildExposure } = require('../lib/exposure');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

router.get('/', async (req, res) => {
  try {
    res.json(await buildExposure(req.user.user_id));
  } catch (err) {
    console.error('Exposure error:', err.message);
    res.status(500).json({ error: 'Exposure unavailable' });
  }
});

module.exports = router;
