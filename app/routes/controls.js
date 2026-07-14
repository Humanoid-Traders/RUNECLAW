/**
 * Live-trading controls (user-facing, JWT-authed).
 *
 * Users adjust their own live-trading settings here: enable/disable live, set a
 * per-trade margin cap, and pause (route trades to paper). These are flags/numbers
 * (not secrets) so they are NOT encrypted; the web queues a `pending_controls`
 * row, the bot PULLS + applies it via its UserStore (the source of truth), then
 * acks back the applied state.
 *
 * Safety: enabling live only flips the user-store flag — the bot's _can_trade_live
 * gate STILL requires the operator's env allowlist, so the web can never grant
 * live access the operator hasn't pre-approved.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');

const router = express.Router();
router.use(authMiddleware);

// Per-user limits on the mutating control endpoints. Emergency stop gets its own
// (slightly tighter) bucket so a settings-spam can't exhaust the stop budget.
const ctlLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });
const stopLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

function secLog(event, req, extra) {
  const uid = req.user && req.user.user_id;
  console.log(`[SECURITY] ${event} user=${uid}${extra ? ' ' + extra : ''}`);
}

// GET /api/controls/status -> current applied state + any pending change
router.get('/status', async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [u] = await pool.execute(
      'SELECT telegram_linked, telegram_id FROM users WHERE id = ?', [uid]);
    const [cur] = await pool.execute(
      'SELECT live_enabled, max_margin, paused, allowlisted FROM user_controls WHERE user_id = ?', [uid]);
    const [pend] = await pool.execute(
      'SELECT live_enabled, max_margin, paused FROM pending_controls WHERE user_id = ?', [uid]);
    const c = cur[0] || {};
    res.json({
      linked: !!(u[0] && u[0].telegram_linked),
      live_enabled: !!c.live_enabled,
      max_margin: c.max_margin != null ? Number(c.max_margin) : null,
      paused: !!c.paused,
      allowlisted: !!c.allowlisted,  // operator pre-approval; live needs this too
      pending: pend.length > 0,
    });
  } catch (err) {
    console.error('Controls status error:', err.message);
    res.status(500).json({ error: 'Failed to read controls' });
  }
});

// POST /api/controls  body: { live_enabled?, max_margin?, paused? } (omit = unchanged)
router.post('/', ctlLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [u] = await pool.execute(
      'SELECT telegram_linked, telegram_id FROM users WHERE id = ?', [uid]);
    if (!u[0] || !u[0].telegram_linked || !u[0].telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Live trading and exchange keys require a linked Telegram account. Paper trading works without it.' });
    }
    const b = req.body || {};
    // Normalise: undefined/missing -> NULL (leave unchanged). Validate types.
    const live = (b.live_enabled === undefined || b.live_enabled === null) ? null : (b.live_enabled ? 1 : 0);
    const paused = (b.paused === undefined || b.paused === null) ? null : (b.paused ? 1 : 0);
    let margin = null;
    if (b.max_margin !== undefined && b.max_margin !== null && b.max_margin !== '') {
      const m = Number(b.max_margin);
      if (!Number.isFinite(m) || m < 0) return res.status(400).json({ error: 'max_margin must be a non-negative number' });
      margin = Math.min(m, 1e9);
    }
    if (live === null && paused === null && margin === null) {
      return res.status(400).json({ error: 'No control changes provided.' });
    }
    await pool.execute(
      `INSERT INTO pending_controls (user_id, telegram_id, live_enabled, max_margin, paused)
       VALUES (?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE telegram_id = VALUES(telegram_id),
         live_enabled = VALUES(live_enabled), max_margin = VALUES(max_margin),
         paused = VALUES(paused), created_at = CURRENT_TIMESTAMP`,
      [uid, String(u[0].telegram_id), live, margin, paused]
    );
    secLog('controls_change', req, `live=${live} paused=${paused} margin=${margin}`);
    res.json({ ok: true, pending: true });
  } catch (err) {
    console.error('Controls submit error:', err.message);
    res.status(500).json({ error: 'Failed to submit controls' });
  }
});

// POST /api/controls/stop -> EMERGENCY STOP: disable live + pause (flag changes,
// applied by the normal control pull) AND queue a flatten of the user's live
// positions (processed asynchronously by the bot via the user's own executor).
router.post('/stop', stopLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const [u] = await pool.execute(
      'SELECT telegram_linked, telegram_id FROM users WHERE id = ?', [uid]);
    if (!u[0] || !u[0].telegram_linked || !u[0].telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Live trading and exchange keys require a linked Telegram account. Paper trading works without it.' });
    }
    const tg = String(u[0].telegram_id);
    // Flag changes: live off + paused. (max_margin left unchanged = NULL.)
    await pool.execute(
      `INSERT INTO pending_controls (user_id, telegram_id, live_enabled, max_margin, paused)
       VALUES (?, ?, ?, ?, ?)
       ON DUPLICATE KEY UPDATE telegram_id = VALUES(telegram_id),
         live_enabled = VALUES(live_enabled), paused = VALUES(paused),
         created_at = CURRENT_TIMESTAMP`,
      [uid, tg, 0, null, 1]
    );
    // Flatten request (own table; the bot acks it only after the close completes).
    await pool.execute(
      `INSERT INTO pending_flatten (user_id, telegram_id) VALUES (?, ?)
       ON DUPLICATE KEY UPDATE telegram_id = VALUES(telegram_id), created_at = CURRENT_TIMESTAMP`,
      [uid, tg]
    );
    secLog('emergency_stop', req);
    res.json({ ok: true, stopping: true });
  } catch (err) {
    console.error('Emergency stop error:', err.message);
    res.status(500).json({ error: 'Failed to queue emergency stop' });
  }
});

module.exports = router;
