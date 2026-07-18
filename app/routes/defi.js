/**
 * DeFi positions — REST surface. JWT-authed, strictly read-only: mirrors
 * the caller's OWN linked wallet's Aave/Lido/Uniswap state. No signing
 * surface exists anywhere behind this router.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { walletAddressOf } = require('../lib/wallet');
const defi = require('../lib/defi');

const router = express.Router();
router.use(authMiddleware);
// Each call can fan out to view calls on several chains — keep it tight.
router.use(rateLimit({ windowMs: 60000, max: 10, key: userKey }));

// GET /api/defi — the caller's DeFi positions with risk warnings.
router.get('/', async (req, res) => {
  try {
    const address = await walletAddressOf(req.user.user_id);
    if (!address) return res.json({ address: null, linked: false });
    const d = await defi.getDefiPositions(address);
    if (!d) return res.status(400).json({ error: 'Linked wallet address unreadable' });
    res.json({ linked: true, ...d });
  } catch (err) {
    console.error('DeFi positions error:', err.message);
    res.status(502).json({ error: 'DeFi read unavailable' });
  }
});

module.exports = router;
