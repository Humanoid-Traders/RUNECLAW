/**
 * Personal what-if replay — REST surface for the Portfolio panel.
 *
 * GET /api/replay?stake=1000&days=90&symbol=BTC
 * JWT-authed. Read-only: replays the agent's recorded closed trades scaled
 * to the caller's hypothetical stake (lib/replay.js). Every response carries
 * hypothetical=true so no renderer can present it as real account history.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { runReplay } = require('../lib/replay');

const router = express.Router();
router.use(authMiddleware);

const replayLimit = rateLimit({ windowMs: 60000, max: 30, key: userKey });

router.get('/', replayLimit, async (req, res) => {
  try {
    const result = await runReplay({
      stake: parseFloat(req.query.stake) || 1000,
      days: parseInt(req.query.days) || 0,
      symbol: String(req.query.symbol || '').slice(0, 12),
    });
    res.json({ hypothetical: true, ...result });
  } catch (err) {
    console.error('Replay error:', err.message);
    res.status(500).json({ error: 'Replay failed' });
  }
});

module.exports = router;
