'use strict';
/**
 * GET /api/public/status — the machine side of the /status trust page.
 * Public by design, rate-limited, and carries no secrets, no account data
 * and no dollar figures (see lib/status.js).
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { buildStatus } = require('../lib/status');

const router = express.Router();
router.use(rateLimit({ windowMs: 60_000, max: 30, key: ipKey, message: 'rate_limited' }));

router.get('/', async (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-cache');
    res.json(await buildStatus());
  } catch (err) {
    // Even the failure mode is honest: the web tier answered, the probe layer
    // did not.
    res.status(200).json({
      status: 'degraded',
      components: { web: { state: 'ok' } },
      error: 'status probes failed',
    });
  }
});

module.exports = router;
