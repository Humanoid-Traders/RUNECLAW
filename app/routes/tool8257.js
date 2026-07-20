'use strict';
/**
 * ERC-8257 tool surface:
 *   GET  /.well-known/ai-tool/runeclaw-intel.json  — the canonical manifest
 *   POST /api/tool/invoke                          — the tool endpoint
 *   GET  /api/tool/registration-plan               — operator dry-run plan
 *
 * The invoke endpoint is a thin dispatcher over the SAME read-only TOOLS
 * registry the /mcp server exposes — one source of truth, so what the
 * on-chain manifest advertises is exactly what /mcp serves. Public data
 * only; nothing here can see an account or place an order.
 */

const express = require('express');
const { rateLimit, ipKey } = require('../lib/rate_limit');
const t8257 = require('../lib/tool8257');

const router = express.Router();
const limited = rateLimit({ windowMs: 60_000, max: 60, key: ipKey, message: 'rate_limited' });

function mcpTools() {
  // Late require avoids a require-cycle at module load (mcp.js is standalone).
  return require('./mcp').TOOLS;
}

router.get('/.well-known/ai-tool/:slug.json', limited, (req, res) => {
  if (req.params.slug !== t8257.TOOL_SLUG) {
    return res.status(404).json({ error: 'unknown tool' });
  }
  const manifest = t8257.buildManifest({ tools: mcpTools() });
  res.json(manifest);
});

router.get('/api/tool/registration-plan', limited, (req, res) => {
  res.json(t8257.buildRegistrationPlan({ tools: mcpTools() }));
});

router.post('/api/tool/invoke', limited, express.json({ limit: '64kb' }), async (req, res) => {
  const body = req.body || {};
  const name = String(body.tool || '');
  const TOOLS = mcpTools();
  const tool = TOOLS[name];
  if (!tool) {
    return res.status(400).json({
      error: `Unknown tool: ${name || '(missing)'}`,
      tools: Object.keys(TOOLS),
    });
  }
  const argErr = require('./mcp').validateArgs(tool.inputSchema, body.args || {});
  if (argErr) return res.status(400).json({ error: argErr });
  try {
    const result = await tool.handler(body.args || {});
    res.json({ tool: name, result });
  } catch (e) {
    res.status(502).json({
      tool: name,
      error: `Tool failed: ${String(e.message || e).slice(0, 200)}`,
    });
  }
});

module.exports = router;
