'use strict';
/**
 * GET /api/public/status — the machine side of the /status trust page.
 * Public by design, rate-limited, and carries no secrets, no account data
 * and no dollar figures (see lib/status.js).
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { buildStatus } = require('../lib/status');
const history = require('../lib/status_history');

const router = express.Router();
router.use(rateLimit({ windowMs: 60_000, max: 30, key: ipKey, message: 'rate_limited' }));

router.get('/', async (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-cache');
    const s = await buildStatus();
    // NB2: record every reading so the 24h timeline fills in from real polls.
    try { history.record(s.status, Date.now()); } catch (e) { /* never block */ }
    res.json(s);
  } catch (err) {
    // Even the failure mode is honest: the web tier answered, the probe layer
    // did not. A probe failure is itself a degraded reading worth recording.
    try { history.record('degraded', Date.now()); } catch (e) { /* ignore */ }
    res.status(200).json({
      status: 'degraded',
      components: { web: { state: 'ok' } },
      error: 'status probes failed',
    });
  }
});

// NB2: GET /api/public/status/history — 24h of overall-status buckets for the
// timeline. Public, no secrets, no dollars. Hourly buckets by default.
router.get('/history', (req, res) => {
  try {
    res.setHeader('Cache-Control', 'no-cache');
    const buckets = history.bucketize(history.samples(), Date.now(), 24, 60 * 60 * 1000);
    res.json({
      window_hours: 24,
      bucket_minutes: 60,
      uptime_pct: history.uptimePct(buckets),
      buckets,
    });
  } catch (err) {
    res.status(200).json({ window_hours: 24, buckets: [], uptime_pct: null });
  }
});

module.exports = router;
