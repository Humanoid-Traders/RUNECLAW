'use strict';
/**
 * PUBLIC community-strategy catalogue — the published user strategies, served
 * with NO auth so anyone browsing /agents sees them alongside the engine's
 * presets. A community strategy is a CONFIG (intent-rule chips + prose) — §4:
 * percent/ratio/list only, never a dollar figure and never a performance claim.
 * Author identity is not exposed. IP-rate-limited and briefly cached.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const store = require('../lib/user_strategies');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey, message: 'rate_limited' }));

// No cache: this reads the local DB (not a slow gateway), so a freshly published
// strategy must appear immediately. The rate limiter already shields the store.
router.get('/', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  try {
    const agents = await store.listPublic(req.query.limit);
    res.json({ agents, count: agents.length, community: true });
  } catch (err) {
    console.error('Public community strategies error:', err.message);
    res.status(502).json({ error: 'Community strategies unavailable' });
  }
});

router.get('/:slug', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  try {
    const agent = await store.getPublicBySlug(req.params.slug);
    if (!agent) return res.status(404).json({ error: 'Strategy not found' });
    res.json({ agent });
  } catch (err) {
    console.error('Public community strategy error:', err.message);
    res.status(502).json({ error: 'Community strategy unavailable' });
  }
});

module.exports = router;
