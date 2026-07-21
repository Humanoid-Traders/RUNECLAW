/**
 * Fixed-term staking (WEB-2) — the /stake fixed flow on the primary surface.
 *
 * OPERATOR-only, and the gateway re-checks the admin role server-side
 * against the bot's own roster — a forged JWT or body can never reach the
 * money path. The double-confirm hard line is enforced by the GATEWAY:
 * the execute call must echo the exact lock END date the UI displayed.
 *
 * Extra web-side gate: when the account has 2FA enrolled, the execute
 * request must carry a fresh TOTP code (same verifier the login flow uses).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');
const { pool } = require('../db');
const totp = require('../lib/totp');

const router = express.Router();
router.use(authMiddleware);

const execLimit = rateLimit({ windowMs: 60000, max: 6, key: userKey });

function notConfigured(res) {
  return res.status(503).json({ error: 'Bot gateway not configured' });
}

// GET /api/staking/fixed — live lock options (gateway 403s non-admins)
router.get('/fixed', async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/staking/fixed?telegram_id=${encodeURIComponent(ident.id)}`, 30000);
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

// POST /api/staking/fixed — the FINAL confirm
// body: { coin, product_id, days, confirm_lock_end, totp_code? }
router.post('/fixed', execLimit, async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  const b = req.body || {};
  const coin = String(b.coin || '').toUpperCase().trim();
  const productId = String(b.product_id || '').trim();
  const days = Number(b.days);
  const confirmEnd = String(b.confirm_lock_end || '').trim();
  if (!coin || !productId || !Number.isInteger(days) || days <= 0 || !confirmEnd) {
    return res.status(400).json({ error: 'bad_request' });
  }
  try {
    // 2FA gate: an enrolled account must present a fresh code for a money
    // move — same verifier as login (lib/totp), fail-closed on bad codes.
    const [rows] = await pool.execute(
      'SELECT totp_enabled, totp_secret FROM users WHERE id = ?',
      [req.user.user_id]);
    const u = rows[0];
    if (u && u.totp_enabled) {
      const code = String(b.totp_code || '').trim();
      if (!code || !totp.verifyTotp(u.totp_secret, code)) {
        return res.status(401).json({
          error: 'two_factor_required',
          detail: 'Enter your 6-digit authenticator code to lock funds.',
        });
      }
    }
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/staking/fixed', {
      telegram_id: ident.id,
      coin, product_id: productId, days,
      confirm_lock_end: confirmEnd,
    }, 45000);
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

module.exports = router;
