/**
 * Web chat — the RUNECLAW chatbot on the website (JWT-authed).
 *
 * Proxies to the bot process's user gateway (POST /gateway/chat), which runs
 * the SAME pipeline as Telegram free-text: intent router -> skill dispatch ->
 * LLM chat fallback, with shared conversation memory. v1 product decision:
 * the bot side only answers admins/operators (403 chat_admin_only otherwise);
 * the UI hides the chat drawer on that error.
 *
 * Identity: web user -> users.telegram_id (must be telegram_linked, same 409
 * pattern as routes/controls.js) -> bot UserStore. The telegram_id is injected
 * server-side; the browser can never chat as someone else.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

const chatLimit = rateLimit({ windowMs: 60000, max: 15, key: userKey });

const MAX_TEXT_LEN = 2000;
// LLM replies can take a while — give chat a longer budget than the default.
const CHAT_TIMEOUT_MS = 45000;

async function linkedTelegram(req, res) {
  const uid = req.user.user_id;
  const [rows] = await pool.execute(
    'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
  const u = rows[0];
  if (!u || !u.telegram_linked || !u.telegram_id) {
    res.status(409).json({ error: 'Link your Telegram first.' });
    return null;
  }
  return u;
}

// POST /api/chat  body: { text }
router.post('/', chatLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const text = typeof (req.body || {}).text === 'string' ? req.body.text.trim() : '';
    if (!text) return res.status(400).json({ error: 'text required' });
    if (text.length > MAX_TEXT_LEN) return res.status(400).json({ error: 'Message too long' });
    const u = await linkedTelegram(req, res);
    if (!u) return;
    const name = String(u.email || '').split('@')[0];
    const r = await gateway.postGateway('/chat', {
      telegram_id: String(u.telegram_id), name, text,
    }, CHAT_TIMEOUT_MS);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Chat proxy error:', err.message);
    return res.status(502).json({ error: 'Chat unavailable' });
  }
});

// GET /api/chat/history?limit=30
router.get('/history', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const u = await linkedTelegram(req, res);
    if (!u) return;
    const limit = Math.min(parseInt(req.query.limit) || 30, 100);
    const r = await gateway.getGateway(
      `/chat/history?telegram_id=${encodeURIComponent(String(u.telegram_id))}&limit=${limit}`);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Chat history proxy error:', err.message);
    return res.status(502).json({ error: 'Chat unavailable' });
  }
});

module.exports = router;
