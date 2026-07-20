'use strict';
/**
 * Read-only NFT surface (OpenSea data): public radar + wallet mirror by
 * address. No marketplace machinery anywhere behind these routes.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const opensea = require('../lib/opensea');

const router = express.Router();
router.use(rateLimit({ windowMs: 60_000, max: 30, key: ipKey, message: 'rate_limited' }));

router.get('/radar', async (req, res) => {
  res.json(await opensea.getNftRadar());
});

router.get('/wallet/:address', async (req, res) => {
  const chain = /^[a-z0-9_-]{1,20}$/.test(String(req.query.chain || ''))
    ? String(req.query.chain) : 'ethereum';
  res.json(await opensea.getWalletNfts(req.params.address, chain));
});

module.exports = router;
