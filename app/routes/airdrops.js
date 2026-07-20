'use strict';
/**
 * Airdrop & Testnet Radar routes.
 *
 * GET /api/airdrops     — public curated radar (no user data, IP-rate-limited).
 * GET /api/airdrops/me  — JWT: the same radar plus honest wallet-readiness
 *                         hints from the caller's OWN linked wallet.
 *
 * Guided-only by design: these endpoints return information and checklists;
 * there is no endpoint that performs, signs, or schedules any on-chain action.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, ipKey, userKey } = require('../lib/rate_limit');
const { getPublicAirdropRadar, getUserAirdropRadar } = require('../lib/airdrops');

const router = express.Router();

router.get('/', rateLimit({ windowMs: 60000, max: 30, key: ipKey }), (req, res) => {
  try {
    res.json(getPublicAirdropRadar());
  } catch (e) {
    res.status(500).json({ error: 'Radar unavailable' });
  }
});

router.get('/me', authMiddleware, rateLimit({ windowMs: 60000, max: 30, key: userKey }), async (req, res) => {
  try {
    res.json(await getUserAirdropRadar(req.user.user_id));
  } catch (e) {
    res.status(500).json({ error: 'Radar unavailable' });
  }
});

module.exports = router;
