/**
 * Research dossiers — REST surface. JWT-authed, read-only; the dossier is
 * composed exclusively from live venue data and recorded platform history
 * (lib/research.js).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { buildDossier } = require('../lib/research');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

router.get('/:symbol', async (req, res) => {
  try {
    const base = String(req.params.symbol || '').toUpperCase()
      .replace(/[^A-Z0-9]/g, '').replace(/USDT$/, '').slice(0, 10);
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
