'use strict';
/**
 * PUBLIC Agent Flight Recorder — the sealed decision ledger served with NO auth.
 * "Don't trust the P&L — read the decision": a prospective user can see the full
 * chain behind every trade (inputs → reasoning + voters → risk verdict → intent
 * policy → geometry → outcome → ledger hash) without an account.
 *
 * §4-safe by construction: every record is passed through sanitizeRecord(),
 * which strips every dollar figure (position size, dollar P&L) — the public
 * surface shows percent / ratio / R-multiple and heuristic risk flags only. The
 * authed /api/guardian/flight keeps the full (dollar-carrying) record.
 *
 * IP-rate-limited and briefly cached so a traffic spike can't hammer the store.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const { getLatestFlight } = require('./sync');
const { inspectWindow, sanitizeRecord } = require('../lib/flight');

const router = express.Router();
router.use(rateLimit({ windowMs: 60000, max: 30, key: ipKey, message: 'rate_limited' }));

const CACHE_MS = 20 * 1000;
let cache = null; // { at, body }

router.get('/', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  const now = Date.now();
  if (cache && (now - cache.at) < CACHE_MS) return res.json(cache.body);
  try {
    const flight = await getLatestFlight();
    if (!flight || !Array.isArray(flight.records)) {
      return res.json({ records: [], chain: null, policy: null, window: null,
        updated_at: (flight && flight.updated_at) || null, note: 'No decisions recorded yet.' });
    }
    let limit = parseInt(req.query.limit, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 50;
    limit = Math.min(limit, 200);
    const records = flight.records.slice(0, limit);
    const body = {
      records: records.map(sanitizeRecord),
      chain: flight.chain || {},
      // The intent policy is already public-safe (rules are percent/ratio caps),
      // but run it through the scrubber too in case a bot adds a dollar field.
      policy: flight.policy ? sanitizeRecord(flight.policy) : null,
      guardian_status: flight.guardian_status || null,
      window: inspectWindow(records),
      updated_at: flight.updated_at || null,
      disclosure: 'Public decision ledger — percent/ratio only, no dollar amounts. '
        + 'Prices are public market data. The chain is engine-verified; the window check is re-derivable below.',
    };
    cache = { at: now, body };
    res.json(body);
  } catch (err) {
    console.error('Public flight error:', err.message);
    res.status(502).json({ error: 'Flight recorder unavailable' });
  }
});

router.get('/:decisionId', async (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  try {
    const flight = await getLatestFlight();
    const records = (flight && Array.isArray(flight.records)) ? flight.records : [];
    const rec = records.find((r) => r && r.decision_id === req.params.decisionId);
    if (!rec) return res.status(404).json({ error: 'Decision not found in recent window' });
    res.json({ record: sanitizeRecord(rec), chain: (flight && flight.chain) || {} });
  } catch (err) {
    console.error('Public flight-by-id error:', err.message);
    res.status(502).json({ error: 'Flight recorder unavailable' });
  }
});

module.exports = router;
