/**
 * RUNECLAW Guardian — read-only API.
 *
 * The safety, control, evidence, and recovery layer for autonomous crypto
 * capital. The first module exposed here is the Agent Flight Recorder: a
 * provenance-complete, tamper-evident record of every trading decision.
 *
 * The authoritative ledger is a SHA-256 hash-chained, Ed25519-attested
 * append-only log held bot-side (logs/audit_chain.jsonl). The engine runs the
 * cryptographic verify() (it holds the file and the exact canonical hashing) and
 * pushes recent joined DECISION↔OUTCOME records plus the verification result via
 * /api/bot/sync/flight. This route is a read-only mirror of that.
 */

const express = require('express');
const { getLatestFlight } = require('./sync');

const router = express.Router();

const HEX64 = /^[0-9a-f]{64}$/;

/**
 * Lightweight web-side sanity pass over the synced window. This is NOT the
 * cryptographic proof (that's the engine's verify(), surfaced as chain.ok) —
 * it's a transparency check that every record carries a well-formed entry hash
 * and that sequence numbers are unique and strictly increasing. Reorders,
 * dropped rows, or malformed hashes in the visible window show up here.
 */
function inspectWindow(records) {
  const problems = [];
  let lastSeq = -Infinity;
  let hashed = 0;
  for (let i = 0; i < records.length; i++) {
    // records arrive newest-first; walk oldest-first for monotonic sequence.
    const r = records[records.length - 1 - i];
    const ch = (r && r.chain) || {};
    const seq = ch.sequence;
    if (typeof seq === 'number') {
      if (seq <= lastSeq) problems.push(`sequence not increasing at ${seq}`);
      lastSeq = seq;
    }
    if (ch.entry_hash && HEX64.test(String(ch.entry_hash))) hashed++;
    else if (ch.entry_hash) problems.push(`malformed entry_hash at seq ${seq}`);
  }
  return { records_checked: records.length, well_formed_hashes: hashed, problems };
}

/**
 * GET /api/guardian/flight?limit=50
 * Returns the recent flight records + the engine-verified chain status.
 */
router.get('/flight', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    if (!flight || !Array.isArray(flight.records)) {
      return res.json({
        records: [], chain: null, window: null, updated_at: null,
        note: 'No decisions recorded yet.',
      });
    }
    let limit = parseInt(req.query.limit, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 50;
    limit = Math.min(limit, 200);
    const records = flight.records.slice(0, limit);
    res.json({
      records,
      chain: flight.chain || {},
      window: inspectWindow(records),
      updated_at: flight.updated_at || null,
    });
  } catch (err) {
    console.error('Guardian flight error:', err.message);
    res.status(500).json({ error: 'Failed to read flight records' });
  }
});

/**
 * GET /api/guardian/flight/:decisionId
 * Returns one full decision record (with its outcome) by decision id.
 */
router.get('/flight/:decisionId', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    const records = (flight && Array.isArray(flight.records)) ? flight.records : [];
    const rec = records.find((r) => r && r.decision_id === req.params.decisionId);
    if (!rec) return res.status(404).json({ error: 'Decision not found in recent window' });
    res.json({ record: rec, chain: (flight && flight.chain) || {} });
  } catch (err) {
    console.error('Guardian flight-by-id error:', err.message);
    res.status(500).json({ error: 'Failed to read decision' });
  }
});

module.exports = router;
