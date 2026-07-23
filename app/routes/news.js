/**
 * News radar — REST surface (NEWS-1c). JWT-authed; a read-only public-RSS
 * headline feed with high-impact flags on the caller's held positions. Advisory
 * only — it flags, it never trades, sizes, or blocks anything.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { getGateway, postGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 12, key: userKey }));

// BYON (NEWS-2): a user's own paid news key rides the encrypted service channel
// straight into the bot's Fernet store — this server never keeps it, and only a
// masked fingerprint ever returns (F-15). Tight write limit for key submissions.
const NEWS_MAX_KEY_LEN = 128;
const keyWriteLimit = rateLimit({ windowMs: 60000, max: 6, key: userKey });

router.get('/', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/news?telegram_id=${encodeURIComponent(ident.id)}`, 15000));
  } catch (err) {
    console.error('News radar error:', err.message);
    res.status(502).json({ error: 'News radar unavailable' });
  }
});

// GET /api/news/key/status — is a BYON key connected? (provider + fingerprint only)
router.get('/key/status', async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(
      `/news/key/status?telegram_id=${encodeURIComponent(ident.id)}`));
  } catch (err) {
    res.status(502).json({ error: 'News radar unavailable' });
  }
});

// POST /api/news/key — connect your own news-provider key  body: { provider, api_key }
router.post('/key', keyWriteLimit, async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  const provider = String((req.body || {}).provider || '').toLowerCase();
  const apiKey = String((req.body || {}).api_key || '').trim();
  if (!provider) return res.status(400).json({ error: 'bad_provider' });
  if (!apiKey || apiKey.length > NEWS_MAX_KEY_LEN) {
    return res.status(400).json({ error: 'bad_key' });
  }
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/news/key', {
      telegram_id: ident.id, provider, api_key: apiKey,
    }));
  } catch (err) {
    res.status(502).json({ error: 'News radar unavailable' });
  }
});

// POST /api/news/key/clear — disconnect your news key
router.post('/key/clear', keyWriteLimit, async (req, res) => {
  if (!isConfigured()) return res.status(503).json({ error: 'Not configured' });
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/news/key/clear', { telegram_id: ident.id }));
  } catch (err) {
    res.status(502).json({ error: 'News radar unavailable' });
  }
});

module.exports = router;
