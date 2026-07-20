/**
 * Share cards — server-rendered PNG for the closed-trade share flow.
 *
 * GET /api/share/card?symbol=&direction=&pnl_pct=
 *
 * The card is a pure function of three public inputs (symbol, direction,
 * PnL percent) rendered by the bot's Pillow renderer and relayed here as
 * binary. It carries NO dollar figure, size, or account data by design —
 * the share flow exists so a win can be shared without leaking account
 * size. JWT-authed (render work is CPU-bound; anonymous callers can't
 * hammer it) but deliberately identity-free: no telegram lookup, no per-
 * user data on this path.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { getGatewayBinary, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

const SYMBOL_RE = /^[A-Z0-9]{1,15}$/;

router.get('/card', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const symbol = String(req.query.symbol || '').toUpperCase().trim();
  const direction = String(req.query.direction || '').toUpperCase().trim();
  const pnl_pct = Number(req.query.pnl_pct);
  if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'Invalid symbol' });
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return res.status(400).json({ error: 'Invalid direction' });
  }
  if (!Number.isFinite(pnl_pct)) return res.status(400).json({ error: 'Invalid pnl_pct' });
  try {
    const qs = new URLSearchParams({ symbol, direction, pnl_pct: pnl_pct.toFixed(2) });
    const r = await getGatewayBinary(`/share-card?${qs.toString()}`, 15000);
    if (r.status !== 200 || !/image\/png/.test(r.contentType)) {
      return res.status(502).json({ error: 'Card unavailable' });
    }
    res.type('png').set('Cache-Control', 'private, max-age=300').send(r.body);
  } catch (err) {
    console.error('Share card error:', err.message);
    res.status(502).json({ error: 'Card unavailable' });
  }
});

module.exports = router;
