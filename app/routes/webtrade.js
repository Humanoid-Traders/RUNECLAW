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
    const ident = await resolveBotIdentity(req);
    // 2FA step-up: a confirm is a real-money move only when the identity is
    // actually LIVE-capable — and that is decided by the BOT (operator allowlist
    // + /live for linked users, or the fail-closed web-live gate for web-only
    // ids), NOT the web-side user_controls.live_enabled mirror. That mirror is
    // written only for web-originated control changes, so it is empty for a user
    // who enabled live in Telegram AND for every web-only live user — keying the
    // step-up off it let those live confirms skip 2FA. Ask the gateway for the
    // authoritative capability (the same _trade_mode the confirm itself uses) and
    // require a fresh code when the account has 2FA enrolled. Paper confirms stay
    // frictionless (important for the one-tap paper flow).
    const [urows] = await pool.execute(
      `SELECT totp_enabled, totp_secret FROM users WHERE id = ?`, [req.user.user_id]);
    const urow = urows[0] || {};
    if (urow.totp_enabled) {
      let liveCapable = true;   // fail SAFE: a gateway hiccup requires the code
      try {
        const lm = await gateway.getGateway(
          `/trade/live_mode?telegram_id=${encodeURIComponent(ident.id)}`, 8000);
        liveCapable = !!(lm && lm.status === 200 && lm.data && lm.data.live_allowed);
      } catch (_) { /* keep liveCapable = true (fail safe) */ }
      if (liveCapable) {
        const blk = stepUpBlock(urow.totp_enabled, urow.totp_secret,
          (req.body || {}).totp_code,
          'Enter your 6-digit authenticator code to confirm a live trade.');
        if (blk) { secLog('WEB_TRADE_CONFIRM_2FA', req, `trade_id=${tradeId}`); return res.status(blk.status).json(blk.body); }
      }
    }
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
