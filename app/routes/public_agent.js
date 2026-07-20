/**
 * PUBLIC agent directory — the ERC-8004 identity card, served with NO auth.
 *
 * GET /api/public/agent/:address
 *
 * Relays the card embedded in the latest sealed publication (the same bundle
 * /proof serves openly), re-verified bot-side on every read. Address format is
 * validated hard before anything reaches the gateway; unknown addresses 404 —
 * the directory only states what a sealed publication backs. IP-rate-limited
 * and briefly cached per address.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const ADDR_RE = /^0x[0-9a-fA-F]{40}$/;
const CACHE_MS = 30 * 1000;
const cache = new Map();       // address -> { at, status, data }

router.get('/:address', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const raw = String(req.params.address || '');
  if (!ADDR_RE.test(raw)) return res.status(400).json({ error: 'Invalid address' });
  const addr = raw.toLowerCase();
  const now = Date.now();
  const hit = cache.get(addr);
  if (hit && (now - hit.at) < CACHE_MS) {
    return res.status(hit.status).json(hit.data);
  }
  try {
    const r = await getGateway(`/public/agent/${addr}`, 15000);
    if (r.status === 200 || r.status === 404) {
      if (cache.size > 64) cache.clear();
      cache.set(addr, { at: now, status: r.status, data: r.data });
    }
    relay(res, r);
  } catch (err) {
    console.error('Public agent error:', err.message);
    res.status(502).json({ error: 'Agent directory unavailable' });
  }
});

module.exports = router;
