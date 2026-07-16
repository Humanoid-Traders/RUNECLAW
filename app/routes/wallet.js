/**
 * On-chain wallet portfolio — REST surface. JWT-authed, strictly read-only:
 * this router can only ever mirror balances of the caller's OWN SIWE-linked
 * wallet. There is no signing surface anywhere behind it.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const wallet = require('../lib/wallet');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

// GET /api/wallet/portfolio — the caller's linked wallet, priced.
router.get('/portfolio', async (req, res) => {
  try {
    const address = await wallet.walletAddressOf(req.user.user_id);
    if (!address) return res.json({ address: null, linked: false });
    const p = await wallet.getWalletPortfolio(address);
    if (!p) return res.status(400).json({ error: 'Linked wallet address unreadable' });
    res.json({ linked: true, ...p });
  } catch (err) {
    console.error('Wallet portfolio error:', err.message);
    res.status(502).json({ error: 'Wallet read unavailable' });
  }
});

module.exports = router;
