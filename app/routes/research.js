/**
 * Research dossiers — REST surface. JWT-authed, read-only; the dossier is
 * composed exclusively from live venue data and recorded platform history
 * (lib/research.js).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');
const { buildDossier } = require('../lib/research');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

const cleanBase = (s) => String(s || '').toUpperCase()
  .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);

// AI-4: opt-in live, CITED web research enrichment for a dossier. Admin-only —
// it bills the operator's AI key and the gateway re-checks the caller's role, so
// a tampered JWT can never spend it. Web search is bursty; keep this limit tight.
router.post('/:symbol/web', rateLimit({ windowMs: 60000, max: 5, key: userKey }),
  async (req, res) => {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Live web research is not configured' });
    }
    const base = cleanBase(req.params.symbol);
    if (!base) return res.status(400).json({ error: 'symbol required' });
    try {
      const ident = await resolveBotIdentity(req);
      const r = await gateway.postGateway('/research/web',
        { telegram_id: ident.id, base }, 30000);
      return gateway.relay(res, r);
    } catch (err) {
      return res.status(502).json({ error: 'Live web research unavailable' });
    }
  });

router.get('/:symbol', async (req, res) => {
  try {
    const base = cleanBase(req.params.symbol);
    if (!base) return res.status(400).json({ error: 'symbol required' });
    const d = await buildDossier(base);
    if (!d) return res.status(404).json({ error: 'Not listed on the venue — no trusted data' });
    res.json(d);
  } catch (err) {
    console.error('Research error:', err.message);
    res.status(500).json({ error: 'Research unavailable' });
  }
});

module.exports = router;
