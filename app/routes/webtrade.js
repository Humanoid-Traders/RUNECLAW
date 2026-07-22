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
 * Identity is resolved server-side (lib/identity.js): linked telegram_id, or
 * "web:<user_id>" for web-only accounts — which the gateway auto-provisions
 * as PAPER-ONLY traders (structurally locked out of live execution).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');
const { pool } = require('../db');
const { stepUpBlock } = require('../lib/stepup');

const router = express.Router();
router.use(authMiddleware);

const tradeLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

const SYMBOL_RE = /^[A-Z0-9]{1,15}$/;
const TRADE_ID_RE = /^[A-Za-z0-9:_\/-]{1,64}$/;

function secLog(event, req, extra) {
  const uid = req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
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
    // Order type: 'market' (open now) or 'limit' (rest at entry). Anything else
    // falls back to 'limit' — the platform default; the gateway re-validates.
    const orderType = String(b.order_type || 'limit').trim().toLowerCase() === 'market' ? 'market' : 'limit';
    const ident = await resolveBotIdentity(req);
    secLog('WEB_TRADE_PROPOSE', req, `${direction} ${symbol} ${orderType} entry=${entry} sl=${sl} tp=${tp}`);
    const r = await gateway.postGateway('/trade/propose', {
      telegram_id: ident.id,
      name: String(ident.email || '').split('@')[0],
      direction, symbol, entry, sl, tp, order_type: orderType,
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
    // 2FA step-up: a confirm on a LIVE-capable account is a real-money move —
    // require a fresh code when the account has 2FA enrolled, so a stolen web
    // session can't place trades. Paper confirms (live_enabled=0) stay
    // frictionless. The gateway still enforces paper-only for web-only accounts.
    const [urows] = await pool.execute(
      `SELECT u.totp_enabled, u.totp_secret, c.live_enabled
         FROM users u LEFT JOIN user_controls c ON c.user_id = u.id
        WHERE u.id = ?`, [req.user.user_id]);
    const urow = urows[0] || {};
    if (urow.live_enabled) {
      const blk = stepUpBlock(urow.totp_enabled, urow.totp_secret,
        (req.body || {}).totp_code,
        'Enter your 6-digit authenticator code to confirm a live trade.');
      if (blk) { secLog('WEB_TRADE_CONFIRM_2FA', req, `trade_id=${tradeId}`); return res.status(blk.status).json(blk.body); }
    }
    const ident = await resolveBotIdentity(req);
    secLog('WEB_TRADE_CONFIRM', req, `trade_id=${tradeId}`);
    const r = await gateway.postGateway('/trade/confirm', {
      telegram_id: ident.id, trade_id: tradeId,
    }, 30000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade confirm proxy error:', err.message);
    return res.status(502).json({ error: 'Trading unavailable' });
  }
});

// POST /api/trade/copilot  body: { direction, symbol, entry, sl, tp, margin? }
// A read-only deterministic second opinion on a proposed trade — advice only.
router.post('/copilot', tradeLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web trading not configured' });
    }
    const b = req.body || {};
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/trade/copilot', {
      telegram_id: ident.id,
      direction: String(b.direction || '').toUpperCase(),
      symbol: String(b.symbol || '').toUpperCase(),
      entry: b.entry, sl: b.sl, tp: b.tp, margin: b.margin,
    }, 12000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade copilot proxy error:', err.message);
    return res.status(502).json({ error: 'Co-pilot unavailable' });
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
    const ident = await resolveBotIdentity(req);
    secLog('WEB_TRADE_CANCEL', req, `trade_id=${tradeId}`);
    const r = await gateway.postGateway('/trade/cancel', {
      telegram_id: ident.id, trade_id: tradeId,
    });
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Trade cancel proxy error:', err.message);
    return res.status(502).json({ error: 'Trading unavailable' });
  }
});

module.exports = router;
