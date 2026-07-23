/**
 * Strategy-Agent follow + paper-copy (Marketplace Phase 3), JWT-authed.
 *
 *   GET  /api/copy          -> { following: [agent_id, ...] }
 *   POST /api/copy/follow   -> follow an agent   { agent_id }
 *   POST /api/copy/unfollow -> unfollow          { agent_id }
 *   GET  /api/copy/picks    -> for each followed agent, the LIVE signals its
 *                              published gates would act on ("would-take"),
 *                              each ready to paper-trade from the trade ticket.
 *
 * §4 / safety: "follow" is a bookmark + a personalised would-take feed. It moves
 * NO funds and places NO trades — copying is user-initiated and paper-only via
 * the normal trade ticket. The picks are derived by applying an agent's own
 * machine-readable gates to the public signal stream; we report exactly which
 * gates matched (the finer intraday gates are applied live by the engine, so we
 * never overclaim). No dollar figures cross this surface.
 */

const express = require('express');
const { pool } = require('../db');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { getGateway, isConfigured } = require('../lib/gateway');
const { picksForAgent } = require('../lib/agent_match');

const router = express.Router();
router.use(authMiddleware);

const writeLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });
const MAX_FOLLOWS = 25;
const AGENT_ID_RE = /^[a-z0-9][a-z0-9-]{0,63}$/;   // catalogue slug

// Short server-side cache of the agent catalogue (it changes only on a deploy).
let catCache = null;   // { at, agents }
const CAT_MS = 5 * 60 * 1000;
async function loadCatalogue() {
  if (!isConfigured()) return [];
  const now = Date.now();
  if (catCache && (now - catCache.at) < CAT_MS) return catCache.agents;
  const r = await getGateway('/public/strategies', 15000);
  if (r.status >= 200 && r.status < 300 && r.data && Array.isArray(r.data.agents)) {
    catCache = { at: now, agents: r.data.agents };
    return catCache.agents;
  }
  return catCache ? catCache.agents : [];
}

async function followingIds(uid) {
  const [rows] = await pool.execute(
    'SELECT agent_id FROM copy_subscriptions WHERE user_id = ? ORDER BY id ASC', [uid]);
  return rows.map(r => r.agent_id);
}

router.get('/', async (req, res) => {
  try {
    res.json({ following: await followingIds(req.user.user_id) });
  } catch (err) {
    console.error('Copy list error:', err.message);
    res.json({ following: [] });
  }
});

router.post('/follow', writeLimit, async (req, res) => {
  try {
    const agentId = String((req.body || {}).agent_id || '').trim().toLowerCase();
    if (!AGENT_ID_RE.test(agentId)) return res.status(400).json({ error: 'invalid agent_id' });
    const uid = req.user.user_id;
    const [c] = await pool.execute(
      'SELECT COUNT(*) AS n FROM copy_subscriptions WHERE user_id = ?', [uid]);
    if (Number(c[0]?.n || 0) >= MAX_FOLLOWS) {
      return res.status(429).json({ error: 'follow_limit', limit: MAX_FOLLOWS });
    }
    await pool.execute(
      `INSERT INTO copy_subscriptions (user_id, agent_id) VALUES (?, ?)
       ON DUPLICATE KEY UPDATE agent_id = VALUES(agent_id)`, [uid, agentId]);
    res.json({ ok: true, following: await followingIds(uid) });
  } catch (err) {
    console.error('Copy follow error:', err.message);
    res.status(500).json({ error: 'follow_failed' });
  }
});

router.post('/unfollow', writeLimit, async (req, res) => {
  try {
    const agentId = String((req.body || {}).agent_id || '').trim().toLowerCase();
    if (!agentId) return res.status(400).json({ error: 'agent_id required' });
    const uid = req.user.user_id;
    await pool.execute(
      'DELETE FROM copy_subscriptions WHERE user_id = ? AND agent_id = ?', [uid, agentId]);
    res.json({ ok: true, following: await followingIds(uid) });
  } catch (err) {
    console.error('Copy unfollow error:', err.message);
    res.status(500).json({ error: 'unfollow_failed' });
  }
});

router.get('/picks', async (req, res) => {
  try {
    const following = await followingIds(req.user.user_id);
    if (!following.length) return res.json({ agents: [], note: '' });

    // Live actionable signals (OPEN, newest first) + the agent catalogue.
    let signals = [];
    try {
      const [rows] = await pool.execute(
        `SELECT signal_key, symbol, direction, confidence, score, pattern, regime,
                entry_price, stop_loss, take_profit, rr, thesis, created_at
         FROM signals WHERE status = ? ORDER BY created_at DESC LIMIT 100`, ['OPEN']);
      signals = rows;
    } catch (e) { /* empty stream is fine */ }

    let catalogue = [];
    try { catalogue = await loadCatalogue(); } catch (e) { catalogue = []; }
    const byId = new Map(catalogue.map(a => [a.id, a]));

    const agents = following.map(id => {
      const a = byId.get(id);
      if (!a) return { id, name: id, icon: '🤖', matched_on: [], picks: [], unavailable: true };
      return picksForAgent(a, signals);
    });
    const note = isConfigured()
      ? 'Picks are the live signals each agent’s published gates would act on — the engine applies its finer intraday entry filters live. Copying is paper-only and you place every trade yourself.'
      : 'The agent catalogue needs the bot bridge to resolve each agent’s gates.';
    res.json({ agents, note });
  } catch (err) {
    console.error('Copy picks error:', err.message);
    res.json({ agents: [], note: '' });
  }
});

module.exports = router;
