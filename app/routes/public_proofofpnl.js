/**
 * PUBLIC Proof-of-PnL — the sealed, re-verifiable track-record statement served
 * with NO auth. "Don't trust the dashboard — verify the fills": a prospective
 * user must be able to see (and independently re-derive) the agent's verifiable
 * performance without an account and without trusting us.
 *
 * The bundle is public-safe by construction — bot/proofofpnl/publish.py refuses
 * to seal anything carrying exchange internals — so serving it openly is
 * deliberate, not a leak. The public page (public/proof.html) re-derives the
 * publish_hash in the visitor's own browser; this endpoint just relays the
 * latest sealed statement from the bot gateway, IP-rate-limited and briefly
 * cached so a traffic spike can't hammer the gateway.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

const CACHE_MS = 30 * 1000;   // the sealer publishes at most once per epoch
let cache = null;              // { at: ms, status, data }

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) {
    return res.status(cache.status).json(cache.data);
  }
  try {
    const r = await getGateway('/public/proofofpnl', 15000);
    // Only cache successful reads; let errors retry immediately.
    if (r.status >= 200 && r.status < 300) cache = { at: now, status: 200, data: r.data };
    relay(res, r);
  } catch (err) {
    console.error('Public Proof-of-PnL error:', err.message);
    res.status(502).json({ error: 'Proof-of-PnL unavailable' });
  }
});

module.exports = router;
