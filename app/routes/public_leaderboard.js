/**
 * PUBLIC verifiable leaderboard — the ranked, anonymous board served with NO
 * auth. Every row is an opted-in agent ranked by its cryptographically
 * re-verifiable record; rows carry only size-agnostic metrics (profit factor,
 * round-trips, Sharpe) and each row's publish_hash — never a dollar figure, so
 * no account size leaks. The ranker (bot/proofofpnl/leaderboard.py) drops any
 * entry that fails re-verification, so serving this openly is deliberate, not a
 * leak. This endpoint just relays the ranked board from the bot gateway,
 * IP-rate-limited and briefly cached so a traffic spike can't hammer it.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 30 * 1000;   // the board refreshes at most once per publish epoch
let cache = null;              // { at: ms, status, data }

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) {
    return res.status(cache.status).json(cache.data);
  }
  try {
    const r = await getGateway('/public/leaderboard', 15000);
    if (r.status >= 200 && r.status < 300) cache = { at: now, status: 200, data: r.data };
    relay(res, r);
  } catch (err) {
    console.error('Public leaderboard error:', err.message);
    res.status(502).json({ error: 'Leaderboard unavailable' });
  }
});

module.exports = router;
