/**
 * Contract Studio — AI Solidity drafting for logged-in users (JWT-authed).
 *
 * Proxies to the bot gateway's POST /gateway/contract/studio, which drafts a
 * Solidity contract with the tier-routed LLM and runs the heuristic security-
 * flag pass over the output. The response is a DRAFT + FLAGS, never an audit or
 * a safety verdict — the disclaimer travels with every reply (§4). No money-path
 * here: this generates and reviews text only (deploy is a separate, gated flow).
 *
 * Identity is resolved server-side (lib/identity.js), so a browser can never
 * generate as someone else. Tighter per-user rate limit than chat — generation
 * is heavier — with the free/paid quota enforced bot-side.
 */

const express = require('express');
const { authMiddleware } = require('../auth');
const { rateLimit, userKey } = require('../lib/rate_limit');
const { resolveBotIdentity } = require('../lib/identity');
const gateway = require('../lib/gateway');

const router = express.Router();
router.use(authMiddleware);

// Generation is token-heavy, so keep the ceiling low; the bot enforces the
// per-day free/paid quota on top of this.
const studioLimit = rateLimit({ windowMs: 60000, max: 8, key: userKey });

const MAX_SPEC_LEN = 2000;
const MAX_SOURCE_LEN = 24000;      // a compiled draft is larger than a spec
const STUDIO_TIMEOUT_MS = 60000;   // drafting a contract can take a while
const COMPILE_TIMEOUT_MS = 45000;  // solc can be slow on a cold binary

// Compilation is CPU-heavy on the bot side, so keep the per-user ceiling low.
const compileLimit = rateLimit({ windowMs: 60000, max: 10, key: userKey });

// POST /api/contract/studio  body: { spec, license?, pragma? }
router.post('/studio', studioLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Contract Studio not configured' });
    }
    const b = req.body || {};
    const spec = typeof b.spec === 'string' ? b.spec.trim()
      : (typeof b.text === 'string' ? b.text.trim() : '');
    if (!spec) return res.status(400).json({ error: 'spec required' });
    if (spec.length > MAX_SPEC_LEN) return res.status(400).json({ error: 'Spec too long' });
    // Short, charset-bounded compiler hints — never arbitrary text into the prompt.
    const license = /^[\w.+-]{1,40}$/.test(String(b.license || '')) ? String(b.license) : undefined;
    const pragma = /^[\d.^><=~\s]{1,16}$/.test(String(b.pragma || '')) ? String(b.pragma).trim() : undefined;

    const ident = await resolveBotIdentity(req);
    const payload = {
      telegram_id: ident.id,
      name: String(ident.email || '').split('@')[0],
      spec,
      ...(license ? { license } : {}),
      ...(pragma ? { pragma } : {}),
    };
    const r = await gateway.postGateway('/contract/studio', payload, STUDIO_TIMEOUT_MS);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Contract Studio proxy error:', err.message);
    return res.status(502).json({ error: 'Contract Studio unavailable' });
  }
});

// POST /api/contract/compile  body: { solidity }
// Compile-check a draft: does it BUILD? Returns bytecode/ABI + solc diagnostics.
// Pure computation on the bot side — no signing, no value movement. The deploy
// flow that consumes the bytecode is a separate, gated slice.
router.post('/compile', compileLimit, async (req, res) => {
  try {
    if (!gateway.isConfigured()) {
      return res.status(503).json({ error: 'Contract Studio not configured' });
    }
    const b = req.body || {};
    const solidity = typeof b.solidity === 'string' ? b.solidity.trim()
      : (typeof b.source === 'string' ? b.source.trim() : '');
    if (!solidity) return res.status(400).json({ error: 'solidity required' });
    if (solidity.length > MAX_SOURCE_LEN) return res.status(400).json({ error: 'Source too long' });

    const ident = await resolveBotIdentity(req);
    const payload = {
      telegram_id: ident.id,
      name: String(ident.email || '').split('@')[0],
      solidity,
      optimize: b.optimize === false ? false : true,
    };
    const r = await gateway.postGateway('/contract/compile', payload, COMPILE_TIMEOUT_MS);
    return gateway.relay(res, r);
  } catch (err) {
    console.error('Contract compile proxy error:', err.message);
    return res.status(502).json({ error: 'Contract compile unavailable' });
  }
});

module.exports = router;
