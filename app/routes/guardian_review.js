/**
 * Guardian pre-trade review queue — admin-only web surface.
 *
 * The safe bridge between the on-chain execution PREVIEW (admin-only dry-run, no
 * signer) and any future signing: every proposed high-risk action is recorded
 * bot-side so a human can review it before a signer slice ever acts on it, and a
 * reviewer can TIGHTEN the standing Authority Envelope. Tightening can only make
 * the envelope MORE restrictive — it can never authorize or loosen anything, and
 * this route never signs or broadcasts. The bot gateway is authoritative on
 * admin + the tighten math; this layer just relays the resolved identity.
 *
 *   GET  /api/guardian/review           → read-only queue (admin)
 *   POST /api/guardian/review/tighten   → narrow a user's envelope (admin)
 */

'use strict';

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

router.get('/', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Review queue not configured' });
    }
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/guardian/review?telegram_id=${encodeURIComponent(ident.id)}`, 12000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Guardian review proxy error:', err.message);
    return res.status(502).json({ error: 'Review queue unavailable' });
  }
});

router.post('/tighten', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Review queue not configured' });
    }
    const b = req.body || {};
    const ident = await resolveBotIdentity(req);
    // The gateway re-checks admin server-side and owns the tighten-only math; the
    // web layer forwards the resolved admin identity, the target, and the spec.
    const r = await gateway.postGateway('/guardian/review/tighten', {
      telegram_id: ident.id,
      target_user: String(b.target_user || ''),
      tighten: (b.tighten && typeof b.tighten === 'object') ? b.tighten : {},
    }, 15000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Guardian tighten proxy error:', err.message);
    return res.status(502).json({ error: 'Tightening unavailable' });
  }
});

module.exports = router;
