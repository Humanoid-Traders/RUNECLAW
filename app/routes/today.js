'use strict';
/**
 * GET /api/today — the public "Today on RUNECLAW" digest (see
 * lib/daily_rune.js). §4: pattern names + confidence, counts and win rates
 * only; anything unavailable is simply absent. 60s in-process cache.
 */

const express = require('express');
const { fetchToday } = require('../lib/daily_rune');

const router = express.Router();

let cache = { at: 0, data: null };

router.get('/', async (req, res) => {
  try {
    if (!cache.data || Date.now() - cache.at > 60_000) {
      cache = { at: Date.now(), data: await fetchToday() };
    }
    res.set('Cache-Control', 'public, max-age=30');
    res.json(cache.data);
  } catch (err) {
    console.error('Today digest error:', err.message);
    res.status(500).json({ error: 'Digest unavailable' });
  }
});

module.exports = router;
