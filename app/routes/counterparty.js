/**
 * Solver & Counterparty Monitor — web surface.
 *
 * Turns the caller's own per-venue / per-chain holdings into a counterparty-
 * concentration read (custodial vs self-custody split, venue/chain HHI, largest
 * single counterparty, settlement-issuer concentration). Reuses buildHoldings
 * so there's one source of truth for the balance fan-out. ADVISORY ONLY (§4).
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const { buildHoldings } = require('../lib/holdings');
const { computeCounterparty } = require('../lib/counterparty');

const router = express.Router();
router.use(authMiddleware);
// Same tight cap as /api/holdings — this fans out the same heavy per-venue +
// wallet reads underneath.
router.use(rateLimit({ windowMs: 60000, max: 6, key: userKey }));

router.get('/', async (req, res) => {
  try {
    const ident = await resolveBotIdentity(req);
    const holdings = await buildHoldings(ident, req.user.user_id);
    res.json({ read_only: true, ...computeCounterparty(holdings), generated_at: holdings.generated_at });
  } catch (err) {
    console.error('Counterparty error:', err.message);
    res.status(502).json({ error: 'Counterparty monitor unavailable' });
  }
});

module.exports = router;
