/**
 * Custom agent alerts — REST surface for the dashboard panel.
 *
 * The chat path ("tell me when…") shares the same lib/alerts.js store ops,
 * so both surfaces enforce the same validation and per-user cap. JWT-authed;
 * every operation is scoped to the caller's own rows. Notification-only —
 * nothing here can touch a trade.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const alerts = require('../lib/alerts');

const router = express.Router();
router.use(authMiddleware);

const writeLimit = rateLimit({ windowMs: 60000, max: 20, key: userKey });
const readLimit = rateLimit({ windowMs: 60000, max: 60, key: userKey });

// GET /api/alerts — the caller's alerts, newest first (active + tripped).
router.get('/', readLimit, async (req, res) => {
  try {
    const rows = await alerts.listAlerts(req.user.user_id);
    res.json({
      alerts: rows.map((a) => ({
        id: a.id,
        symbol: String(a.symbol || ''),
        metric: a.metric,
        op: a.op,
        threshold: Number(a.threshold),
        label: alerts.describeCondition(a),
        active: !!Number(a.active),
        trigger_price: a.trigger_price != null ? Number(a.trigger_price) : null,
        created_at: a.created_at,
        triggered_at: a.triggered_at,
      })),
      max_active: alerts.MAX_ACTIVE_PER_USER,
    });
  } catch (err) {
    console.error('Alerts list error:', err.message);
    res.status(500).json({ error: 'Failed to load alerts' });
  }
});

// POST /api/alerts  body: { symbol, metric, op, threshold }
router.post('/', writeLimit, async (req, res) => {
  try {
    const b = req.body || {};
    const r = await alerts.createAlert(req.user.user_id, {
      base: String(b.symbol || ''),
      metric: String(b.metric || 'price'),
      op: b.op === '<' || b.op === '>' ? b.op : null,
      threshold: Number(b.threshold),
      inferOp: !b.op,
    });
    if (!r.ok) return res.status(400).json({ error: r.error });
    res.json({ ok: true, label: alerts.describeCondition(r.alert), now: r.now });
  } catch (err) {
    console.error('Alert create error:', err.message);
    res.status(500).json({ error: 'Failed to create alert' });
  }
});

// DELETE /api/alerts/:id — own rows only.
router.delete('/:id', writeLimit, async (req, res) => {
  try {
    const ok = await alerts.deleteAlert(req.user.user_id, req.params.id);
    if (!ok) return res.status(404).json({ error: 'Alert not found' });
    res.json({ ok: true });
  } catch (err) {
    console.error('Alert delete error:', err.message);
    res.status(500).json({ error: 'Failed to delete alert' });
  }
});

module.exports = router;
