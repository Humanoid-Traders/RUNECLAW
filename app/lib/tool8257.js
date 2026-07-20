'use strict';
/**
 * ERC-8257 Agent Tool Registry — RUNECLAW's tool manifest + registration plan.
 *
 * ERC-8257 (draft, authored by OpenSea) is a permissionless on-chain registry
 * where AI agents discover tools: each record binds a creator address, a
 * metadata URI and a manifest hash, with an optional access-predicate
 * contract. It layers on ERC-8004 (agent identity) — which RUNECLAW anchors
 * on Base — so registering here makes our verifiable intelligence
 * discoverable by other agents on the same chain as our identity root.
 *
 * Hard lines, enforced structurally (tests grep this file):
 * - NON-CUSTODIAL: this module builds a DRY-RUN registration plan (calldata
 *   included). The OPERATOR signs it from their own wallet — the server never
 *   holds a key, never signs, never broadcasts.
 * - FREE + OPEN: the manifest carries NO `pricing` block and NO `access`
 *   predicate. Per-call charging is x402 payment machinery, which stays
 *   design-only behind the four independent gates in docs/INTEROP.md §4.
 * - READ-ONLY: the tool endpoint dispatches ONLY to the public MCP tool set —
 *   the same read-only handlers the /mcp server exposes. No accounts, no
 *   orders, no funds.
 *
 * Manifest contract (mirrors @opensea/tool-sdk v0.28, inspected 2026-07):
 * - schema: { type, name, description, version, endpoint, tags, inputs,
 *   outputs, creatorAddress, [verifiability] } — inputs/outputs are JSON
 *   Schema objects.
 * - served at /.well-known/ai-tool/<slug>.json
 * - manifestHash = keccak256(utf8(JCS(manifest))) — RFC 8785 canonical JSON.
 * - registerTool(string metadataURI, bytes32 manifestHash, address
 *   accessPredicate) on the canonical ToolRegistry.
 */

const { ethers } = require('ethers');

// Canonical ToolRegistry deployment (same address cross-chain; Base 8453 is
// where our ERC-8004 identity root lives, Ethereum 1 is the promotion path).
const TOOL_REGISTRY_ADDRESS = '0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1';
const REGISTRY_CHAINS = { 8453: 'Base', 1: 'Ethereum' };
const ZERO_ADDRESS = '0x' + '00'.repeat(20);
const MANIFEST_TYPE = 'https://ercs.ethereum.org/ERCS/erc-8257#tool-manifest-v1';
const TOOL_SLUG = 'runeclaw-intel';

const REGISTER_ABI = [
  'function registerTool(string metadataURI, bytes32 manifestHash, address accessPredicate) returns (uint256 toolId)',
];

/**
 * RFC 8785 (JCS) canonicalization for the manifest we author: recursively
 * sorted object keys, no insignificant whitespace, UTF-8. JSON.stringify's
 * number serialization is the ECMAScript algorithm JCS specifies, and we
 * refuse non-finite numbers, so this reproduces the SDK's `canonicalize`
 * output byte-for-byte for this data.
 */
function jcs(value) {
  if (value === null || typeof value === 'boolean' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (typeof value === 'number') {
    if (!isFinite(value)) throw new Error('non-finite number in manifest');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return '[' + value.map(jcs).join(',') + ']';
  if (typeof value === 'object') {
    const keys = Object.keys(value).filter(k => value[k] !== undefined).sort();
    return '{' + keys.map(k => JSON.stringify(k) + ':' + jcs(value[k])).join(',') + '}';
  }
  throw new Error(`unsupported manifest value type: ${typeof value}`);
}

function manifestHash(manifest) {
  return ethers.keccak256(ethers.toUtf8Bytes(jcs(manifest)));
}

function baseUrl() {
  return (process.env.APP_BASE_URL || process.env.WEBSITE_URL || '')
    .trim().replace(/\/+$/, '');
}

function creatorAddress() {
  const a = (process.env.TOOL_CREATOR_ADDRESS
    || process.env.PROOFOFPNL_AGENT_ADDRESS || '').trim().toLowerCase();
  return /^0x[0-9a-f]{40}$/.test(a) ? a : null;
}

/**
 * The manifest for RUNECLAW's single registered tool: a dispatcher over the
 * public read-only MCP tool set, so the on-chain record and /mcp can never
 * drift — both are generated from the same TOOLS registry at request time.
 */
function buildManifest({ tools }) {
  const base = baseUrl() || 'https://runeclaw.example';
  const creator = creatorAddress() || ZERO_ADDRESS;
  return {
    type: MANIFEST_TYPE,
    name: TOOL_SLUG,
    description: 'RUNECLAW read-only trading intelligence: cryptographically '
      + 'verifiable track record (Proof-of-PnL with re-derivable hashes), '
      + 'ERC-8004 agent identity cards, engine signals, tamper-evident flight '
      + 'records, token research + deterministic safety reads, and sector '
      + 'radars (RWA, meme, airdrops). Every tool serves data the public site '
      + 'already publishes — no accounts, no orders, no funds. Free and open: '
      + 'no pricing, no access gate. Past performance never predicts future '
      + 'results.',
    version: '1.0.0',
    endpoint: `${base}/api/tool/invoke`,
    tags: ['trading', 'crypto', 'verifiable-track-record', 'proof-of-pnl',
      'erc-8004', 'read-only', 'intelligence'],
    inputs: {
      type: 'object',
      properties: {
        tool: {
          type: 'string',
          enum: Object.keys(tools),
          description: 'Which read-only intelligence tool to invoke.',
        },
        args: {
          type: 'object',
          description: 'Arguments for the selected tool (see per-tool schema '
            + 'via MCP tools/list at the /mcp endpoint of the same host).',
        },
      },
      required: ['tool'],
    },
    outputs: {
      type: 'object',
      properties: {
        tool: { type: 'string' },
        result: { type: 'object', description: 'The selected tool\'s JSON result.' },
      },
    },
    creatorAddress: creator,
    verifiability: {
      tier: 'self-attested',
      execution: 'Express server backed by recorded trading history; the '
        + 'Proof-of-PnL statement and identity card it serves are '
        + 'independently re-derivable (documented canonicalization + hashes).',
      dataRetention: 'none',
      sourceVisibility: 'open-source',
      reproducibleBuild: {
        sourceCodeURI: 'https://github.com/Humanoid-Traders/RUNECLAW',
      },
    },
  };
}

/**
 * DRY RUN ONLY — the registerTool transaction the operator sends from their
 * own wallet. accessPredicate is the zero address: open access, no gate, no
 * charge. Never signs, never broadcasts.
 */
function buildRegistrationPlan({ tools }) {
  const manifest = buildManifest({ tools });
  const hash = manifestHash(manifest);
  const metadataURI = `${baseUrl() || 'https://runeclaw.example'}`
    + `/.well-known/ai-tool/${TOOL_SLUG}.json`;
  const iface = new ethers.Interface(REGISTER_ABI);
  const calldata = iface.encodeFunctionData('registerTool',
    [metadataURI, hash, ZERO_ADDRESS]);
  const creator = creatorAddress();
  return {
    dry_run: true,
    ready: Boolean(creator && baseUrl()),
    not_ready_reasons: [
      ...(creator ? [] : ['set TOOL_CREATOR_ADDRESS (or PROOFOFPNL_AGENT_ADDRESS)']),
      ...(baseUrl() ? [] : ['set APP_BASE_URL so metadataURI resolves publicly']),
    ],
    registry: TOOL_REGISTRY_ADDRESS,
    chains: Object.entries(REGISTRY_CHAINS)
      .map(([id, name]) => ({ chain_id: Number(id), name })),
    recommended_chain_id: 8453,
    metadata_uri: metadataURI,
    manifest_hash: hash,
    access_predicate: ZERO_ADDRESS,
    access_note: 'zero address = open access. No NFT gate, no per-call charge '
      + '— pricing/x402 stays design-only behind the four gates in '
      + 'docs/INTEROP.md §4.',
    calldata,
    instructions: [
      `Send from your own wallet (ideally the creator address ${creator || '<unset>'}) `
        + `to the ToolRegistry ${TOOL_REGISTRY_ADDRESS} on Base — value 0, `
        + 'data = the calldata field above.',
      'Or with foundry: cast send ' + TOOL_REGISTRY_ADDRESS
        + ` "registerTool(string,bytes32,address)" "${metadataURI}" ${hash} `
        + `${ZERO_ADDRESS} --rpc-url https://mainnet.base.org `
        + '--private-key <YOUR_KEY_NEVER_SHARED_WITH_THE_SERVER>',
      'The manifest served at the metadata URI must stay byte-identical for '
        + 'the on-chain hash to keep verifying — re-register (or '
        + 'update-metadata) after any manifest change.',
    ],
    non_custodial_note: 'The server never holds a key and never sends a '
      + 'transaction. This is a plan, not an action.',
  };
}

module.exports = {
  TOOL_REGISTRY_ADDRESS,
  ZERO_ADDRESS,
  MANIFEST_TYPE,
  TOOL_SLUG,
  jcs,
  manifestHash,
  buildManifest,
  buildRegistrationPlan,
};
