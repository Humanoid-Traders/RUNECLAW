/**
 * PUBLIC Agent Letter — permalinks to the weekly letter with NO auth.
 *
 * GET /api/public/letter/latest   — last completed week
 * GET /api/public/letter/archive  — recent week keys (keys only)
 * GET /api/public/letter/:week    — a specific completed week (e.g. 2026-W28)
 *
 * The payload is the DOLLAR-FREE recomposition (lib/letter.getPublicLetter):
 * counts, win rate, profit factor, equity percent change, regime reads —
 * never a dollar figure, so account size cannot leak from a shared letter.
 * The private (JWT-authed) letter at /api/letter is unchanged. IP-rate-limited
 * and cached per completed (immutable) week.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const letters = require('../lib/letter');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey }));

router.get('/latest', async (req, res) => {
  try {
    const week = letters.lastCompletedWeek();
    const pub = await letters.getPublicLetter(week.key);
    if (!pub) return res.status(404).json({ error: 'No letter yet' });
    res.json({ letter: pub });
  } catch (err) {
    console.error('Public letter latest error:', err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

router.get('/archive', async (req, res) => {
  try {
    const rows = await letters.listLetters(12);
    res.json({ weeks: rows.map(r => r.week_key) });
  } catch (err) {
    console.error('Public letter archive error:', err.message);
    res.status(500).json({ error: 'Archive unavailable' });
  }
});

router.get('/:week', async (req, res) => {
  try {
    const pub = await letters.getPublicLetter(req.params.week);
    if (!pub) return res.status(404).json({ error: 'No letter for that week' });
    res.json({ letter: pub });
  } catch (err) {
    console.error('Public letter fetch error:', err.message);
    res.status(500).json({ error: 'Letter unavailable' });
  }
});

module.exports = router;
