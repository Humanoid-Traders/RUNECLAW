'use strict';
/** Read-only spot market surface. No order machinery behind these routes. */
const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const spot = require('../lib/spot');

const router = express.Router();
router.use(rateLimit({ windowMs: 60_000, max: 30, key: ipKey, message: 'rate_limited' }));

router.get('/market', async (req, res) => res.json(await spot.getSpotMarket()));
router.get('/basis', async (req, res) => res.json(await spot.getSpotPerpBasis()));

module.exports = router;
