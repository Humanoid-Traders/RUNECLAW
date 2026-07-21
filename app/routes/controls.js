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
const { postGateway, relay, isConfigured } = require('../lib/gateway');
const { stepUpBlock } = require('../lib/stepup');

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
      'SELECT telegram_linked, telegram_id, totp_enabled, totp_secret FROM users WHERE id = ?', [uid]);
    if (!u[0] || !u[0].telegram_linked || !u[0].telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Live trading and exchange keys require a linked Telegram account. Paper trading works without it.' });
    }
    const b = req.body || {};
    // Normalise: undefined/missing -> NULL (leave unchanged). Validate types.
    const live = (b.live_enabled === undefined || b.live_enabled === null) ? null : (b.live_enabled ? 1 : 0);
    // 2FA step-up: ENABLING live trading is the money-unlock — require a fresh
    // code when 2FA is enrolled. Disabling live, pausing, and lowering margin
    // stay frictionless so de-risking is never gated (the /stop path never is).
    if (live === 1) {
      const blk = stepUpBlock(u[0].totp_enabled, u[0].totp_secret, b.totp_code,
        'Enter your 6-digit authenticator code to enable live trading.');
      if (blk) { secLog('controls_enable_2fa', req); return res.status(blk.status).json(blk.body); }
    }
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

// POST /api/controls/stance  body: { mode } — queue a GLOBAL strategy-stance
// change (defensive/balanced/aggressive/manual). Admin plan only: stance is
// the operator bot's global posture, not a per-user setting. Plan is re-read
// fresh from the DB (not trusted from the JWT), and the bot re-verifies the
// requester's tier against its OWN UserStore before applying — the web can
// propose, only the bot decides.
const STANCE_MODES = new Set(['defensive', 'balanced', 'aggressive', 'manual']);
router.post('/stance', ctlLimit, async (req, res) => {
  try {
    const uid = req.user.user_id;
    const mode = String((req.body || {}).mode || '').toLowerCase();
    if (!STANCE_MODES.has(mode)) {
      return res.status(400).json({ error: 'mode must be one of defensive|balanced|aggressive|manual' });
    }
    const [u] = await pool.execute(
      'SELECT telegram_linked, telegram_id, plan FROM users WHERE id = ?', [uid]);
    if (!u[0] || String(u[0].plan) !== 'admin') {
      secLog('stance_denied', req, `mode=${mode}`);
      return res.status(403).json({ error: 'admin_required', detail: 'Stance is the agent\'s global posture — only the operator (admin plan) can change it.' });
    }
    if (!u[0].telegram_linked || !u[0].telegram_id) {
      return res.status(409).json({ error: 'telegram_required', detail: 'Link your Telegram account first so the bot can verify the request.' });
    }
    await pool.execute(
      'REPLACE INTO pending_stance (id, mode, requested_by, telegram_id) VALUES (1, ?, ?, ?)',
      [mode, uid, String(u[0].telegram_id)]);
    secLog('stance_queued', req, `mode=${mode}`);
    res.json({ ok: true, pending: true, mode });
  } catch (err) {
    console.error('Stance submit error:', err.message);
    res.status(500).json({ error: 'Failed to queue stance change' });
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

// ── Intent Compiler authoring (operator only) ──────────────────────────────
//
// Web parity for the Telegram /policy compile→preview→confirm→bind loop. The
// policy is the agent's GLOBAL, tighten-only risk-gate control, so — exactly
// like /stance above — plan is re-read fresh from the DB (never trusted from the
// JWT), a linked Telegram is required, and the request is forwarded to the bot
// gateway which re-verifies the caller is admin before it compiles or binds
// anything. The web proposes; the bot decides.
async function operatorGate(req, res) {
  const uid = req.user.user_id;
  const [u] = await pool.execute(
    'SELECT telegram_linked, telegram_id, plan FROM users WHERE id = ?', [uid]);
  if (!u[0] || String(u[0].plan) !== 'admin') {
    secLog('policy_denied', req);
    res.status(403).json({ error: 'admin_required', detail: 'The intent policy is the agent\'s global risk-gate control — only the operator (admin plan) can author it.' });
    return null;
  }
  if (!u[0].telegram_linked || !u[0].telegram_id) {
    res.status(409).json({ error: 'telegram_required', detail: 'Link your Telegram account first so the bot can verify the request.' });
    return null;
  }
  if (!isConfigured()) {
    res.status(503).json({ error: 'gateway_unconfigured', detail: 'The bot gateway is not configured, so policy authoring is unavailable right now.' });
    return null;
  }
  return String(u[0].telegram_id);
}

// POST /api/controls/policy/preview  body: { text } — compile a preview (no bind)
router.post('/policy/preview', ctlLimit, async (req, res) => {
  try {
    const tg = await operatorGate(req, res);
    if (!tg) return;
    const text = String((req.body || {}).text || '').slice(0, 600);
    if (!text.trim()) return res.status(400).json({ error: 'text required' });
    const r = await postGateway('/policy/preview', { telegram_id: tg, text }, 15000);
    return relay(res, r);
  } catch (err) {
    console.error('Policy preview error:', err.message);
    res.status(502).json({ error: 'Policy preview failed' });
  }
});

// POST /api/controls/policy/apply  body: { text, mode } — compile + BIND
router.post('/policy/apply', ctlLimit, async (req, res) => {
  try {
    const tg = await operatorGate(req, res);
    if (!tg) return;
    const b = req.body || {};
    const text = String(b.text || '').slice(0, 600);
    const mode = String(b.mode || 'shadow').toLowerCase();
    if (!text.trim()) return res.status(400).json({ error: 'text required' });
    if (!['shadow', 'enforce'].includes(mode)) return res.status(400).json({ error: 'mode must be shadow or enforce' });
    const r = await postGateway('/policy/apply', { telegram_id: tg, text, mode }, 15000);
    secLog('policy_apply', req, `mode=${mode}`);
    return relay(res, r);
  } catch (err) {
    console.error('Policy apply error:', err.message);
    res.status(502).json({ error: 'Policy apply failed' });
  }
});

// POST /api/controls/policy/mode  body: { mode } — off|shadow|enforce
router.post('/policy/mode', ctlLimit, async (req, res) => {
  try {
    const tg = await operatorGate(req, res);
    if (!tg) return;
    const mode = String((req.body || {}).mode || '').toLowerCase();
    if (!['off', 'shadow', 'enforce'].includes(mode)) return res.status(400).json({ error: 'mode must be off, shadow or enforce' });
    const r = await postGateway('/policy/mode', { telegram_id: tg, mode }, 12000);
    secLog('policy_mode', req, `mode=${mode}`);
    return relay(res, r);
  } catch (err) {
    console.error('Policy mode error:', err.message);
    res.status(502).json({ error: 'Policy mode change failed' });
  }
});

// POST /api/controls/policy/clear — remove the bound policy
router.post('/policy/clear', ctlLimit, async (req, res) => {
  try {
    const tg = await operatorGate(req, res);
    if (!tg) return;
    const r = await postGateway('/policy/clear', { telegram_id: tg }, 12000);
    secLog('policy_clear', req);
    return relay(res, r);
  } catch (err) {
    console.error('Policy clear error:', err.message);
    res.status(502).json({ error: 'Policy clear failed' });
  }
});

module.exports = router;
