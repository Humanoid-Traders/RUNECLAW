'use strict';
/**
 * User-authored strategies — REST surface for the builder. JWT-authed; every
 * operation is scoped to the caller's own rows. Building/publishing a strategy
 * is web-side config only — it never touches a trade or the bot.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const store = require('../lib/user_strategies');

const router = express.Router();
router.use(authMiddleware);

const writeLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });
const readLimit = rateLimit({ windowMs: 60000, max: 90, key: userKey });

// GET /api/strategies — the caller's own strategies (drafts + published).
router.get('/', readLimit, async (req, res) => {
  try {
    res.json({
      strategies: await store.listMine(req.user.user_id),
      max: store.MAX_PER_USER, max_public: store.MAX_PUBLIC_PER_USER,
    });
  } catch (err) {
    console.error('Strategies list error:', err.message);
    res.status(500).json({ error: 'Failed to load strategies' });
  }
});

// POST /api/strategies — create a draft.
router.post('/', writeLimit, async (req, res) => {
  try {
    const r = await store.create(req.user.user_id, req.body || {});
    if (!r.ok) return res.status(400).json({ error: r.error });
    res.json({ ok: true, slug: r.slug });
  } catch (err) {
    console.error('Strategy create error:', err.message);
    res.status(500).json({ error: 'Failed to create strategy' });
  }
});

// PUT /api/strategies/:id — edit own strategy.
router.put('/:id', writeLimit, async (req, res) => {
  try {
    const r = await store.update(req.user.user_id, req.params.id, req.body || {});
    if (!r.ok) return res.status(r.error === 'Strategy not found.' ? 404 : 400).json({ error: r.error });
    res.json({ ok: true });
  } catch (err) {
    console.error('Strategy update error:', err.message);
    res.status(500).json({ error: 'Failed to update strategy' });
  }
});

// DELETE /api/strategies/:id — own rows only.
router.delete('/:id', writeLimit, async (req, res) => {
  try {
    const ok = await store.remove(req.user.user_id, req.params.id);
    if (!ok) return res.status(404).json({ error: 'Strategy not found' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Strategy delete error:', err.message);
    res.status(500).json({ error: 'Failed to delete strategy' });
  }
});

// POST /api/strategies/:id/publish  and  /unpublish
router.post('/:id/publish', writeLimit, async (req, res) => {
  try {
    const r = await store.setVisibility(req.user.user_id, req.params.id, 'public');
    if (!r.ok) return res.status(r.error === 'Strategy not found.' ? 404 : 400).json({ error: r.error });
    res.json({ ok: true, visibility: r.visibility });
  } catch (err) {
    console.error('Strategy publish error:', err.message);
    res.status(500).json({ error: 'Failed to publish strategy' });
  }
});
router.post('/:id/unpublish', writeLimit, async (req, res) => {
  try {
    const r = await store.setVisibility(req.user.user_id, req.params.id, 'draft');
    if (!r.ok) return res.status(404).json({ error: r.error });
    res.json({ ok: true, visibility: r.visibility });
  } catch (err) {
    console.error('Strategy unpublish error:', err.message);
    res.status(500).json({ error: 'Failed to unpublish strategy' });
  }
});

module.exports = router;
