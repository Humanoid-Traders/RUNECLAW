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
const { inspectWindow } = require('../lib/flight');

const router = express.Router();

/**
 * GET /api/guardian/flight?limit=50
 * Returns the recent flight records + the engine-verified chain status.
 */
router.get('/flight', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    if (!flight || !Array.isArray(flight.records)) {
      return res.json({
        records: [], chain: null, guardian_status: (flight && flight.guardian_status) || null,
        window: null, updated_at: (flight && flight.updated_at) || null,
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
      policy: flight.policy || null,
      guardian_status: flight.guardian_status || null,
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

/**
 * GET /api/guardian/incidents?limit=40
 * The safety-engine incident ledger — blocks (firewall / risk-gate rejection /
 * auth-denied / self-critique halt / intent-policy deny) and recoveries (escape
 * plans), plus twin/sentinel flags — mirrored read-only from the sealed chain.
 * Falls back to deriving blocks from REJECTED flight records for older bots that
 * don't yet send an `incidents` array. Percent/flags only, no dollar amounts.
 */
router.get('/incidents', async (req, res) => {
  try {
    const flight = await getLatestFlight();
    let limit = parseInt(req.query.limit, 10);
    if (!Number.isFinite(limit) || limit < 1) limit = 40;
    limit = Math.min(limit, 100);

    let incidents = (flight && Array.isArray(flight.incidents)) ? flight.incidents : null;
    let derived = false;
    if (!incidents) {
      // Fallback: surface rejected decisions as block incidents.
      const recs = (flight && Array.isArray(flight.records)) ? flight.records : [];
      incidents = recs
        .filter((r) => r && String(r.outcome || '').startsWith('REJECTED'))
        .map((r) => ({
          id: (r.chain && r.chain.entry_hash) || r.decision_id || '',
          ts: r.timestamp || '',
          kind: 'block', category: 'Risk-gate rejection', severity: 'high',
          symbol: r.symbol || '',
          detail: (r.risk && (r.risk.reason
            || (Array.isArray(r.risk.checks_failed) && r.risk.checks_failed[0]))) || 'rejected',
          chain: r.chain || null,
        }));
      derived = true;
    }
    incidents = incidents.slice(0, limit);
    const tally = { block: 0, recovery: 0, flag: 0 };
    for (const i of incidents) { if (i && tally[i.kind] !== undefined) tally[i.kind]++; }

    res.json({
      read_only: true,
      incidents,
      counts: tally,
      derived,                          // true = fallback (rejections only)
      guardian_status: (flight && flight.guardian_status) || null,
      updated_at: (flight && flight.updated_at) || null,
      note: incidents.length ? undefined
        : 'No safety incidents recorded — the controls have had nothing to stop or recover.',
    });
  } catch (err) {
    console.error('Guardian incidents error:', err.message);
    res.status(500).json({ error: 'Failed to read incidents' });
  }
});

module.exports = router;
