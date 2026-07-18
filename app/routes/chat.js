/**
 * Web chat — the RUNECLAW chatbot on the website (JWT-authed, ALL users).
 *
 * Proxies to the bot process's user gateway (POST /gateway/chat), which runs
 * the SAME pipeline as Telegram free-text: intent router -> skill dispatch ->
 * LLM chat fallback, with shared conversation memory and per-role LLM tiers.
 *
 * Identity: resolved server-side (lib/identity.js) — the linked telegram_id,
 * or "web:<user_id>" for web-only accounts (paper-only, auto-provisioned by
 * the bot). The browser can never chat as someone else.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');
const { loadProfile } = require('./profile');
const { maybeHandleAlertChat } = require('../lib/alerts');
const { maybeHandleReplayChat } = require('../lib/replay');
const { maybeHandleLetterChat } = require('../lib/letter');
const { maybeHandleRwaChat } = require('../lib/rwa');
const { maybeHandleWalletChat } = require('../lib/wallet');
const { maybeHandleDefiChat } = require('../lib/defi');
const { maybeHandleNetWorthChat } = require('../lib/networth');
const { maybeHandleExposureChat } = require('../lib/exposure');
const { maybeHandleResearchChat } = require('../lib/research');

const router = express.Router();
router.use(authMiddleware);

const chatLimit = rateLimit({ windowMs: 60000, max: 15, key: userKey });

const MAX_TEXT_LEN = 2000;
// LLM replies can take a while — give chat a longer budget than the default.
const CHAT_TIMEOUT_MS = 45000;

// POST /api/chat  body: { text }
router.post('/', chatLimit, async (req, res) => {
  try {
    const text = typeof (req.body || {}).text === 'string' ? req.body.text.trim() : '';
    if (!text) return res.status(400).json({ error: 'text required' });
    if (text.length > MAX_TEXT_LEN) return res.status(400).json({ error: 'Message too long' });
    // "tell me when BTC drops below 100k" — alerts live in the WEB app (the
    // push channel is here), so handle them before the bot proxy. Evaluated
    // against public tickers; works even while the bot process is down.
    const alertReply = await maybeHandleAlertChat(req.user.user_id, text);
    if (alertReply) return res.json(alertReply);
    // "what if I'd taken every signal with $1k?" — replayed from the web's
    // own recorded trade history, no bot round-trip needed.
    const replayReply = await maybeHandleReplayChat(req.user.user_id, text);
    if (replayReply) return res.json(replayReply);
    // "show me this week's letter" — the weekly fund-style letter, composed
    // from recorded data in the web DB.
    const letterReply = await maybeHandleLetterChat(req.user.user_id, text);
    if (letterReply) return res.json(letterReply);
    // "rwa radar" — read-only tokenized-asset sector snapshot from live tickers.
    const rwaReply = await maybeHandleRwaChat(req.user.user_id, text);
    if (rwaReply) return res.json(rwaReply);
    // "my wallet" — read-only mirror of the caller's SIWE-linked wallet.
    const walletReply = await maybeHandleWalletChat(req.user.user_id, text);
    if (walletReply) return res.json(walletReply);
    // "my defi positions" / "health factor" — Aave/Lido/Uniswap read straight
    // from protocol contracts, with liquidation-risk warnings.
    const defiReply = await maybeHandleDefiChat(req.user.user_id, text);
    if (defiReply) return res.json(defiReply);
    // "what's my total exposure?" — perp positions netted against wallet spot.
    const exposureReply = await maybeHandleExposureChat(req.user.user_id, text);
    if (exposureReply) return res.json(exposureReply);
    // "research PENDLE" — evidence dossier from trusted local + live sources.
    const researchReply = await maybeHandleResearchChat(req.user.user_id, text);
    if (researchReply) return res.json(researchReply);
    // "net worth" — everything the user holds, everywhere, read-only.
    // Needs the resolved bot identity, so it runs after resolveBotIdentity…
    // except the identity is resolved below; resolve it here for this
    // intercept only when the pattern matches (cheap guard inside).
    if (/net ?worth|total (balance|holdings|equity)|balance across|everything i (own|hold)/i.test(text)) {
      const identNW = await resolveBotIdentity(req);
      const nwReply = await maybeHandleNetWorthChat(identNW, req.user.user_id, text);
      if (nwReply) return res.json(nwReply);
    }
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Chat not configured' });
    }
    const ident = await resolveBotIdentity(req);
    const name = String(ident.email || '').split('@')[0];
    // The user's saved agent profile rides along so the bot's chat prompt
    // knows who it's talking to (risk preference, watchlist). Best-effort —
    // a profile read hiccup must never block chat.
    let profile = null;
    try {
      const p = await loadProfile(req.user.user_id);
      if (p.risk_pref || (p.watchlist || []).length) {
        profile = { risk_pref: p.risk_pref, watchlist: p.watchlist };
      }
    } catch (e) { /* chat works without a profile */ }
    const r = await gateway.postGateway('/chat', {
      telegram_id: ident.id, name, text,
      ...(profile ? { profile } : {}),
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
    const ident = await resolveBotIdentity(req);
    const limit = Math.min(parseInt(req.query.limit) || 30, 100);
    const r = await gateway.getGateway(
      `/chat/history?telegram_id=${encodeURIComponent(ident.id)}&limit=${limit}`);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Chat history proxy error:', err.message);
    return res.status(502).json({ error: 'Chat unavailable' });
  }
});

module.exports = router;
