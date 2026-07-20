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
const cache = new Map();       // key ('' | season) -> { at: ms, status, data }

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  // Season standings (frozen calendar-month windows) share the relay; the
  // key is validated hard so the cache can't be poisoned by junk params.
  const rawSeason = String(req.query.season || '').slice(0, 7);
  const season = /^\d{4}-\d{2}$/.test(rawSeason) ? rawSeason : '';
  const now = Date.now();
  const hit = cache.get(season);
  if (hit && (now - hit.at) < CACHE_MS) {
    return res.status(hit.status).json(hit.data);
  }
  try {
    const qs = season ? `?season=${encodeURIComponent(season)}` : '';
    const r = await getGateway(`/public/leaderboard${qs}`, 15000);
    if (r.status >= 200 && r.status < 300) {
      if (cache.size > 32) cache.clear();          // bound the map
      cache.set(season, { at: now, status: 200, data: r.data });
    }
    relay(res, r);
  } catch (err) {
    console.error('Public leaderboard error:', err.message);
    res.status(502).json({ error: 'Leaderboard unavailable' });
  }
});

module.exports = router;
