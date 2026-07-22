/**
 * POST /api/web3/execute — WEB3-LIVE-EXEC slice 1 (admin-only, preview-only).
 *
 * The first, safest slice toward live on-chain execution: it returns a DRY-RUN
 * PREVIEW of an on-chain action and NEVER signs or broadcasts. The bot gateway
 * re-checks admin server-side (a forged JWT can't reach it), runs the web3
 * execution gate (default-OFF flag, testnet-first) and the Authority Envelope
 * authorize() before producing the preview. Real signing/broadcast ships in a
 * later, separately-gated, still admin-only, still envelope-enforced slice.
 */

'use strict';

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);
router.use(rateLimit({ windowMs: 60000, max: 10, key: userKey }));

router.post('/execute', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web3 execution not configured' });
    }
    const b = req.body || {};
    const ident = await resolveBotIdentity(req);
    // The gateway is authoritative on admin + gate + envelope; the web layer just
    // forwards the resolved identity and the requested (preview) action.
    const r = await gateway.postGateway('/web3/execute', {
      telegram_id: ident.id,
      network: String(b.network || 'sepolia'),
      side: String(b.side || 'swap'),
      from_token: String(b.from_token || ''),
      to_token: String(b.to_token || ''),
      amount_usd: b.amount_usd,
      dest: String(b.dest || ''),
      // broadcast is intentionally NOT forwarded — this slice is preview-only and
      // the gate refuses a broadcast anyway.
    }, 15000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Web3 execute preview proxy error:', err.message);
    return res.status(502).json({ error: 'Web3 execution unavailable' });
  }
});

/**
 * POST /api/web3/sign — WEB3-LIVE-EXEC slice 2 (admin-only, TESTNET-ONLY).
 *
 * The first slice that actually SIGNS + broadcasts an on-chain transaction — a
 * native-value transfer to an envelope-allowlisted destination, on a testnet
 * only. The bot gateway is authoritative: it re-checks admin, runs the signing
 * gate (its own default-OFF flag + a configured key + the eth-account library +
 * an enforcing envelope), runs authorize(), signs, and broadcasts to the
 * configured testnet RPC. The signing key never leaves the bot; the web layer
 * only forwards the resolved identity and the transfer parameters.
 */
router.post('/sign', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web3 signing not configured' });
    }
    const b = req.body || {};
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/web3/sign', {
      telegram_id: ident.id,
      network: String(b.network || 'sepolia'),
      to: String(b.to || b.dest || ''),
      value_wei: b.value_wei,
      nonce: b.nonce,
      gas: b.gas,
      // Prepared EIP-1559 fees from /web3/sign/prepare (optional; bot defaults otherwise).
      max_fee_wei: b.max_fee_wei,
      max_priority_wei: b.max_priority_wei,
      amount_usd: b.amount_usd,
      asset: String(b.asset || 'ETH'),
    }, 20000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Web3 sign proxy error:', err.message);
    return res.status(502).json({ error: 'Web3 signing unavailable' });
  }
});

/**
 * GET /api/web3/sign/status — admin-only, read-only signer status for the web UI.
 *
 * Surfaces the signing flags, whether the eth-account library + key are present,
 * the signer's PUBLIC address, per-testnet RPC readiness, and whether an enforcing
 * envelope is bound. The gateway re-checks admin server-side. The private key
 * never leaves the bot and is never part of this payload.
 */
router.get('/sign/status', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web3 signing not configured' });
    }
    const ident = await resolveBotIdentity(req);
    const q = encodeURIComponent(String(ident.id || ''));
    const r = await gateway.getGateway(`/web3/sign/status?telegram_id=${q}`, 12000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Web3 sign status proxy error:', err.message);
    return res.status(502).json({ error: 'Web3 signing unavailable' });
  }
});

/**
 * POST /api/web3/sign/prepare — admin-only, TESTNET-ONLY nonce/gas prepare.
 *
 * Auto-fetches the next nonce + EIP-1559 gas fees for the signer address from the
 * configured testnet RPC, so the send form never needs a hand-computed nonce. The
 * gateway runs the same fail-closed signing gate first, reads only public chain
 * state, and never signs. No key ever crosses this relay.
 */
router.post('/sign/prepare', async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Web3 signing not configured' });
    }
    const b = req.body || {};
    const ident = await resolveBotIdentity(req);
    const r = await gateway.postGateway('/web3/sign/prepare', {
      telegram_id: ident.id,
      network: String(b.network || 'sepolia'),
    }, 15000);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Web3 sign prepare proxy error:', err.message);
    return res.status(502).json({ error: 'Web3 signing unavailable' });
  }
});

module.exports = router;
