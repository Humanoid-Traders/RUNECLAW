/**
 * The Agent Letter — read surface for the dashboard panel.
 *
 * GET /api/letter/latest       — last completed week's letter (lazy-generated)
 * GET /api/letter/archive      — recent week keys
 * GET /api/letter/:week        — a specific stored letter (e.g. 2026-W28)
 *
 * JWT-authed. Read-only; letters are composed exclusively from recorded data.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const letters = require('../lib/letter');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 30, key: userKey }));

router.get('/latest', async (req, res) => {
  try {
    const r = await letters.getLetter(letters.lastCompletedWeek());
    res.json({ generated_at: r.generated_at, letter: r.letter });
  } catch (err) {
    console.error('Letter latest error:', err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

router.get('/archive', async (req, res) => {
  try {
    res.json({ letters: await letters.listLetters(12) });
  } catch (err) {
    console.error('Letter archive error:', err.message);
    res.status(500).json({ error: 'Archive unavailable' });
  }
});

router.get('/:week', async (req, res) => {
  try {
    const r = await letters.getLetterByKey(req.params.week);
    if (!r) return res.status(404).json({ error: 'No letter for that week' });
    res.json(r);
  } catch (err) {
    console.error('Letter fetch error:', err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

module.exports = router;
