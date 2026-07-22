/**
 * dApp connectors hub — REST surface. PUBLIC (no user data), IP rate-limited.
 * Serves a curated, read-only directory of reputable DeFi/NFT dApps with
 * deep-links to each dApp's own official site. RUNECLAW never routes or
 * executes anything from here (§4: links/recommendations only).
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { listDapps, CATEGORIES, chains, NOTE } = require('../lib/dapps');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 60, key: ipKey }));

// GET /api/dapps?category=&chain= — the curated directory (optionally filtered).
router.get('/', (req, res) => {
  const dapps = listDapps({ category: req.query.category, chain: req.query.chain });
  res.json({ read_only: true, dapps, categories: CATEGORIES, chains: chains(), note: NOTE });
});

module.exports = router;
