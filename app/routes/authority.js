/**
 * Authority Envelope authoring — REST surface (per-user, self-serve).
 *
 * A user types, in plain words, what their agent may do; the bot compiles it to
 * a hashed, tighten-only Authority Envelope bound to their identity. An
 * enforce-mode envelope is the custody precondition for live web trading.
 *
 * JWT-authed; identity resolved server-side (resolveBotIdentity → linked
 * telegram_id or web:<id>). Every mutation RECOMPILES bot-side from the text —
 * the browser never sends a policy blob the bot would trust.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { postGateway, getGateway, relay, isConfigured } = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 20, key: userKey }));

const TEXT_MAX = 600;

function guard(res) {
  if (!isConfigured()) { res.status(503).json({ error: 'Not configured' }); return false; }
  return true;
}

// POST /api/authority/preview  { text } — compile a preview, no bind.
router.post('/preview', async (req, res) => {
  if (!guard(res)) return;
  try {
    const text = String((req.body || {}).text || '').slice(0, TEXT_MAX);
    if (!text.trim()) return res.status(400).json({ error: 'text required' });
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/authority/preview', { telegram_id: ident.id, text }, 15000));
  } catch (e) { res.status(502).json({ error: 'Authority preview unavailable' }); }
});

// POST /api/authority/apply  { text, mode } — compile + BIND.
router.post('/apply', async (req, res) => {
  if (!guard(res)) return;
  try {
    const b = req.body || {};
    const text = String(b.text || '').slice(0, TEXT_MAX);
    const mode = ['off', 'shadow', 'enforce'].includes(b.mode) ? b.mode : 'shadow';
    if (!text.trim()) return res.status(400).json({ error: 'text required' });
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/authority/apply', { telegram_id: ident.id, text, mode }, 15000));
  } catch (e) { res.status(502).json({ error: 'Authority apply unavailable' }); }
});

// POST /api/authority/mode  { mode } — off|shadow|enforce.
router.post('/mode', async (req, res) => {
  if (!guard(res)) return;
  try {
    const mode = String((req.body || {}).mode || '');
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/authority/mode', { telegram_id: ident.id, mode }, 12000));
  } catch (e) { res.status(502).json({ error: 'Authority mode unavailable' }); }
});

// POST /api/authority/revoke — human kill-switch.
router.post('/revoke', async (req, res) => {
  if (!guard(res)) return;
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await postGateway('/authority/revoke', { telegram_id: ident.id }, 12000));
  } catch (e) { res.status(502).json({ error: 'Authority revoke unavailable' }); }
});

// GET /api/authority/status — bound envelope + live-ready checklist.
router.get('/', async (req, res) => {
  if (!guard(res)) return;
  try {
    const ident = await resolveBotIdentity(req);
    relay(res, await getGateway(`/authority/status?telegram_id=${encodeURIComponent(ident.id)}`, 12000));
  } catch (e) { res.status(502).json({ error: 'Authority status unavailable' }); }
});

module.exports = router;
