/**
 * LLM connect (WEB-1) — plug your own AI into your agent, from the website.
 *
 * Proxies to the bot gateway's /gateway/llm endpoints. The browser never
 * talks to the gateway directly and this server never STORES the key: it
 * rides the authenticated service channel straight into the bot's
 * Fernet-encrypted user_settings store, and only a fingerprint ever comes
 * back. Identity is resolved server-side (lib/identity.js) exactly like
 * chat — the linked telegram_id, or "web:<user_id>" for web-only accounts.
 *
 * The ULTRA toggle is admin-only and the gateway re-checks the caller's
 * role server-side (bot UserStore + ADMIN_TELEGRAM_IDS) — a tampered JWT
 * or forged body can never flip routing for anyone else.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

// Key submissions are sensitive + infrequent — tight limit.
const writeLimit = rateLimit({ windowMs: 60000, max: 6, key: userKey });

const PROVIDERS = ['openai', 'anthropic', 'gemini', 'groq', 'mistral',
  'deepseek', 'together', 'openrouter', 'alibaba'];
const MAX_KEY_LEN = 512;

function notConfigured(res) {
  return res.status(503).json({ error: 'Bot gateway not configured' });
}

// GET /api/llm — connection status (fingerprint only, never the key)
router.get('/', async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.getGateway(
      `/llm?telegram_id=${encodeURIComponent(ident.id)}`);
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

// POST /api/llm — connect your own provider key  body: { provider, api_key }
router.post('/', writeLimit, async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  const provider = String((req.body || {}).provider || '').toLowerCase();
  const apiKey = String((req.body || {}).api_key || '').trim();
  if (!PROVIDERS.includes(provider)) {
    return res.status(400).json({ error: 'bad_provider', providers: PROVIDERS });
  }
  if (!apiKey || apiKey.length > MAX_KEY_LEN) {
    return res.status(400).json({ error: 'bad_key' });
  }
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/llm', {
      telegram_id: ident.id, provider, api_key: apiKey,
    });
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

// POST /api/llm/clear — disconnect your key
router.post('/clear', writeLimit, async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/llm/clear',
      { telegram_id: ident.id });
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

// POST /api/llm/ultra — ADMIN-only ULTRA routing toggle  body: { enabled }
router.post('/ultra', writeLimit, async (req, res) => {
  if (!gateway.isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/llm/ultra', {
      telegram_id: ident.id,
      enabled: Boolean((req.body || {}).enabled),
    });
    return gateway.relay(res, r);
  } catch (e) {
    return res.status(502).json({ error: 'Bot gateway unavailable' });
  }
});

module.exports = router;
