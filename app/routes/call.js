'use strict';
/**
 * GET /api/call/:key — Provable Calls verify feed (public).
 *
 * Serves the sealed receipt for one signal: the seal (sha256 hex), the
 * canonical decision-time payload EXACTLY as sealed, when it was sealed,
 * the row's CURRENT decision-time values (so the client can surface any
 * drift — there should never be any), and the outcome. The client
 * re-derives sha256(seal_payload) with WebCrypto and compares — the server
 * is never trusted to say "verified".
 */

const express = require('express');
const { pool } = require('../db');

const router = express.Router();

const KEY_RE = /^[A-Za-z0-9:_.\-]{4,128}$/;

router.get('/:key', async (req, res) => {
  try {
    const key = String(req.params.key || '');
    if (!KEY_RE.test(key)) return res.status(400).json({ error: 'Invalid call id' });
    const [rows] = await pool.execute(
      'SELECT signal_key, symbol, direction, confidence, pattern, regime, entry_price, stop_loss, take_profit, status, pnl, created_at, resolved_at, seal, seal_payload, sealed_at FROM signals WHERE signal_key = ?',
      [key]);
    const s = rows[0];
    if (!s || !s.seal) {
      return res.status(404).json({ error: 'No sealed call with that id — receipts exist for calls made after Provable Calls shipped.' });
    }
    res.set('Cache-Control', 'public, max-age=15');
    res.json({
      seal: s.seal,
      seal_payload: s.seal_payload,
      sealed_at: s.sealed_at,
      current: {
        signal_key: s.signal_key, symbol: s.symbol, direction: s.direction,
        entry_price: Number(s.entry_price) || 0,
        stop_loss: Number(s.stop_loss) || 0,
        take_profit: Number(s.take_profit) || 0,
        confidence: Number(s.confidence) || 0,
        pattern: s.pattern || null, regime: s.regime || null,
        created_at: s.created_at,
      },
      outcome: { status: s.status, pnl: s.pnl == null ? null : Number(s.pnl), resolved_at: s.resolved_at },
    });
  } catch (err) {
    console.error('Call verify error:', err.message);
    res.status(500).json({ error: 'Verify feed unavailable' });
  }
});

module.exports = router;
