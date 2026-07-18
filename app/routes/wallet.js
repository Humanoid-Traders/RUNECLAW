/**
 * On-chain wallet portfolio — REST surface. JWT-authed, strictly read-only:
 * this router can only ever mirror balances of the caller's OWN SIWE-linked
 * wallet. There is no signing surface anywhere behind it.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const wallet = require('../lib/wallet');
const solana = require('../lib/solana');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

// GET /api/wallet/portfolio — the caller's linked wallet(s), priced. EVM
// chains come from the SIWE-linked address; a Solana section joins when the
// user has set a watch address (both read-only mirrors).
router.get('/portfolio', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [rows] = await pool.execute('SELECT * FROM users WHERE id = ?', [uid]);
    const u = rows[0] || {};
    const address = u.wallet_address || null;
    const solAddress = u.sol_address || null;
    if (!address && !solAddress) return res.json({ address: null, linked: false });

    const p = address ? await wallet.getWalletPortfolio(address) : null;
    if (address && !p) return res.status(400).json({ error: 'Linked wallet address unreadable' });

    // Solana fails soft: an RPC hiccup degrades that section, never the view.
    let sol = null;
    if (solAddress) {
      try { sol = await solana.getSolanaPortfolio(solAddress); } catch (e) { sol = null; }
    }

    const chains = [...(p ? p.chains : []), ...(sol ? sol.chains : [])];
    const assets = [...(p ? p.assets : []), ...(sol ? sol.assets : [])]
      .sort((a, b) => (b.usd || 0) - (a.usd || 0));
    res.json({
      linked: true,
      read_only: true,
      address,
      sol_address: solAddress,
      chain: 'multi',
      chains,
      assets,
      total_usd: Math.round(((p ? p.total_usd : 0) + (sol ? sol.total_usd : 0)) * 100) / 100,
      unpriced: (p ? p.unpriced : 0) + (sol ? sol.unpriced : 0),
      generated_at: new Date().toISOString(),
    });
  } catch (err) {
    console.error('Wallet portfolio error:', err.message);
    res.status(502).json({ error: 'Wallet read unavailable' });
  }
});

module.exports = router;
