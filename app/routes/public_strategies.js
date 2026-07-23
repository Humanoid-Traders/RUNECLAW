/**
 * PUBLIC Strategy-Agent marketplace catalogue — served with NO auth so anyone
 * can browse the engine's strategy agents. Each card is one of the real engine
 * presets and carries DESIGN + regime + qualitative risk only — never a dollar
 * figure and never a fabricated return (§4). Verified performance lives on the
 * honest Strategy Lab backtester + the verifiable leaderboard. Relayed from the
 * bot gateway, IP-rate-limited and briefly cached so a spike can't hammer it.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 5 * 60 * 1000;   // the catalogue changes only on a deploy
let cache = null;                  // { at: ms, data }

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) {
    return res.status(200).json(cache.data);
  }
  try {
    const r = await getGateway('/public/strategies', 15000);
    if (r.status >= 200 && r.status < 300) {
      cache = { at: now, data: r.data };
    }
    relay(res, r);
  } catch (err) {
    res.status(502).json({ error: 'Strategy catalogue unavailable' });
  }
});

module.exports = router;
