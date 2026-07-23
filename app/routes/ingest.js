/**
 * NEWS-3: personal ingest — "share with your agent".
 *
 * A user pastes text they already have (a newsletter they received, an article
 * excerpt) and it becomes PRIVATE context for THEIR OWN agent. Proxies the bot
 * gateway's /gateway/ingest endpoints: the text rides the authenticated service
 * channel straight into the bot's Fernet-encrypted per-user store — this server
 * never keeps it. Identity is resolved server-side (lib/identity), so the
 * browser can never read or write another user's notes.
 *
 * §4: the platform never FETCHES anything here — the user supplies the text, so
 * there is no paywalled-scraping path — and nothing is ever redistributed or
 * shown on a public / community surface.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, postGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

const MAX_BODY = 20000;
const writeLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });

function notConfigured(res) {
  return res.status(503).json({ error: 'Not configured' });
}

// GET /api/ingest — the caller's own shared notes (preview only, private)
router.get('/', rateLimit({ windowMs: 60000, max: 30, key: userKey }), async (req, res) => {
  if (!isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/ingest?telegram_id=${encodeURIComponent(ident.id)}`));
  } catch (err) {
    res.status(502).json({ error: 'Ingest unavailable' });
  }
});

// POST /api/ingest — share a note with your agent  body: { title?, body, source? }
router.post('/', writeLimit, async (req, res) => {
  if (!isConfigured()) return notConfigured(res);
  const b = req.body || {};
  const text = String(b.body || '').trim();
  if (!text) return res.status(400).json({ error: 'empty', detail: 'Nothing to save.' });
  if (text.length > MAX_BODY) {
    return res.status(400).json({ error: 'too_long', detail: 'That note is too long.' });
  }
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/ingest', {
      telegram_id: ident.id,
      title: String(b.title || '').slice(0, 200),
      body: text,
      source: String(b.source || '').slice(0, 200),
    }));
  } catch (err) {
    res.status(502).json({ error: 'Ingest unavailable' });
  }
});

// POST /api/ingest/delete — forget one note (or all)  body: { id? }
router.post('/delete', writeLimit, async (req, res) => {
  if (!isConfigured()) return notConfigured(res);
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/ingest/delete', {
      telegram_id: ident.id, id: (req.body || {}).id,
    }));
  } catch (err) {
    res.status(502).json({ error: 'Ingest unavailable' });
  }
});

module.exports = router;
