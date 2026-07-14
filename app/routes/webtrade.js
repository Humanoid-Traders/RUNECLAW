/**
 * Web manual trading (JWT-authed) — propose / confirm / cancel a manual trade
 * from the website, riding the bot's user gateway.
 *
 * Safety model (identical to Telegram /trade):
 *   propose -> the bot registers a PENDING idea (nothing executes),
 *   confirm -> engine.confirm_trade re-runs the risk gate and routes to the
 *   paper portfolio, or live ONLY if the user passes the bot's _can_trade_live
 *   (operator env allowlist AND user-store flag). The gateway also enforces
 *   proposer isolation: a web user can only confirm/cancel their own proposals.
 *
 * The telegram_id is resolved server-side from the JWT user's linked account
 * (409 when not linked — same pattern as routes/controls.js).
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

const tradeLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

const SYMBOL_RE = /^[A-Z0-9]{1,15}$/;
const TRADE_ID_RE = /^[A-Za-z0-9:_\/-]{1,64}$/;

function secLog(event, req, extra) {
  const uid = req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
}

async function linkedTelegram(req, res) {
  const uid = req.user.user_id;
  const [rows] = await pool.execute(
    'SELECT telegram_id, telegram_linked FROM users WHERE id = ?', [uid]);
  const u = rows[0];
  if (!u || !u.telegram_linked || !u.telegram_id) {
    res.status(409).json({ error: 'Link your Telegram first.' });
    return null;
  }
  return u;
}

function finitePositive(v) {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? n : null;
}

// POST /api/trade/propose  body: { direction, symbol, entry, sl, tp, margin? }
router.post('/propose', tradeLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web trading not configured' });
    }
    const b = req.body || {};
    const direction = String(b.direction || '').trim().toUpperCase();
    const symbol = String(b.symbol || '').trim().toUpperCase();
    const entry = finitePositive(b.entry);
    const sl = finitePositive(b.sl);
    const tp = finitePositive(b.tp);
    let margin;
    if (b.margin !== undefined && b.margin !== null && b.margin !== '') {
      margin = finitePositive(b.margin);
      if (margin === null) return res.status(400).json({ error: 'margin must be a positive number' });
    }
    if (!['LONG', 'SHORT'].includes(direction)) {
      return res.status(400).json({ error: 'direction must be LONG or SHORT' });
    }
    if (!SYMBOL_RE.test(symbol)) return res.status(400).json({ error: 'Invalid symbol' });
    if (entry === null || sl === null || tp === null) {
      return res.status(400).json({ error: 'entry, sl, tp must be positive numbers' });
    }
    const u = await linkedTelegram(req, res);
    if (!u) return;
    secLog('WEB_TRADE_PROPOSE', req, `${direction} ${symbol} entry=${entry} sl=${sl} tp=${tp}`);
    const r = await gateway.postGateway('/trade/propose', {
      telegram_id: String(u.telegram_id),
      direction, symbol, entry, sl, tp,
      ...(margin !== undefined ? { margin } : {}),
    });
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade propose proxy error:', err.message);
    return res.status(502).json({ error: 'Trading unavailable' });
  }
});

// POST /api/trade/confirm  body: { trade_id }
router.post('/confirm', tradeLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web trading not configured' });
    }
    const tradeId = String((req.body || {}).trade_id || '').trim();
    if (!TRADE_ID_RE.test(tradeId)) return res.status(400).json({ error: 'Invalid trade_id' });
    const u = await linkedTelegram(req, res);
    if (!u) return;
    secLog('WEB_TRADE_CONFIRM', req, `trade_id=${tradeId}`);
    const r = await gateway.postGateway('/trade/confirm', {
      telegram_id: String(u.telegram_id), trade_id: tradeId,
    }, 30000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade confirm proxy error:', err.message);
    return res.status(502).json({ error: 'Trading unavailable' });
  }
});

// POST /api/trade/cancel  body: { trade_id }
router.post('/cancel', tradeLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web trading not configured' });
    }
    const tradeId = String((req.body || {}).trade_id || '').trim();
    if (!TRADE_ID_RE.test(tradeId)) return res.status(400).json({ error: 'Invalid trade_id' });
    const u = await linkedTelegram(req, res);
    if (!u) return;
    secLog('WEB_TRADE_CANCEL', req, `trade_id=${tradeId}`);
    const r = await gateway.postGateway('/trade/cancel', {
      telegram_id: String(u.telegram_id), trade_id: tradeId,
    });
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade cancel proxy error:', err.message);
    return res.status(502).json({ error: 'Trading unavailable' });
  }
});

module.exports = router;
