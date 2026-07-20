'use strict';
/**
 * Airdrop & Testnet Radar (PR SS) — GUIDED, never automated.
 *
 * What this is: a curated catalog of live airdrop/testnet campaigns, an
 * honest read-only eligibility mirror over the user's OWN linked wallet, and
 * a per-campaign guided checklist where THE USER performs and signs every
 * step themselves.
 *
 * What this is deliberately NOT (hard product lines, do not "improve" these
 * away): no automated participation, no transaction signing, no wallet
 * generation, no multi-wallet orchestration, no activity generated for the
 * sole purpose of qualifying for rewards. Airdrop programs actively hunt and
 * retroactively disqualify botted/sybil activity — automation here would be
 * both against most programs' terms and the fastest way to burn users' gas
 * for nothing. The radar informs; the human participates.
 *
 * Freshness: campaigns churn. The built-in seed is a hand-curated snapshot
 * (see CURATED_AT) and every entry carries its official link — verify there
 * before acting. Operators can replace the catalog without a deploy via
 * AIRDROP_CATALOG_PATH (a JSON file with the same shape).
 */

const fs = require('fs');
const { getWalletPortfolio, walletAddressOf } = require('./wallet');

const CURATED_AT = '2026-01';

// Built-in seed catalog. Shape per entry:
//   key, name, project_type, chains[] (keys matching lib/wallet CHAINS where
//   applicable, or 'testnet'), status ('live'|'expected'|'points'),
//   costs ('free'|'gas-only'|'capital'), effort ('low'|'medium'|'high'),
//   requirements[], steps[] (the guided checklist — human actions),
//   official_url, notes.
const SEED_CATALOG = [
  {
    key: 'monad-testnet',
    name: 'Monad testnet',
    project_type: 'L1 (parallel EVM)',
    chains: ['testnet'],
    status: 'live',
    costs: 'free',
    effort: 'low',
    requirements: ['An EVM wallet you own', 'Testnet tokens from the official faucet'],
    steps: [
      'Open the official site and confirm the URL from the project\'s verified socials',
      'Request testnet funds from the official faucet (never pay for testnet tokens)',
      'Use the network genuinely: deploy, swap, or try the dApps you actually find interesting',
      'Keep using the SAME wallet you\'d want any future recognition on',
    ],
    official_url: 'https://testnet.monad.xyz',
    notes: 'No airdrop is promised. Genuine testnet feedback is the activity that matters.',
  },
  {
    key: 'megaeth-testnet',
    name: 'MegaETH testnet',
    project_type: 'Ethereum L2 (real-time)',
    chains: ['testnet'],
    status: 'live',
    costs: 'free',
    effort: 'low',
    requirements: ['An EVM wallet you own', 'Faucet testnet ETH'],
    steps: [
      'Verify the official testnet URL via the project\'s verified channels',
      'Claim faucet funds and try the ecosystem apps yourself',
      'Report real bugs/feedback through official channels — that is what testnets reward, when they reward anything',
    ],
    official_url: 'https://testnet.megaeth.com',
    notes: 'No airdrop is promised.',
  },
  {
    key: 'base-onchain',
    name: 'Base onchain activity',
    project_type: 'Ethereum L2 (Coinbase)',
    chains: ['base'],
    status: 'expected',
    costs: 'gas-only',
    effort: 'low',
    requirements: ['A funded wallet on Base (gas)'],
    steps: [
      'Bridge a small amount you actually intend to use to Base via the official bridge',
      'Use apps you genuinely want — swaps, mints, names — as yourself, on your one wallet',
    ],
    official_url: 'https://base.org',
    notes: 'Base has never promised a token. Treat any activity as its own reward, not a farm.',
  },
  {
    key: 'hyperliquid-ecosystem',
    name: 'HyperEVM ecosystem',
    project_type: 'DEX L1 ecosystem',
    chains: ['ethereum'],
    status: 'points',
    costs: 'capital',
    effort: 'medium',
    requirements: ['Capital you can afford to deploy', 'Understanding of each app\'s risk'],
    steps: [
      'Research each HyperEVM app\'s points program on its official docs',
      'Deploy only capital whose loss you can absorb — points never justify bad risk',
      'Track your positions in the Portfolio view like any other exposure',
    ],
    official_url: 'https://hyperliquid.xyz',
    notes: 'Ecosystem apps run points programs; the base HYPE airdrop already happened (Nov 2024).',
  },
  {
    key: 'arbitrum-open',
    name: 'Arbitrum ecosystem incentives',
    project_type: 'Ethereum L2',
    chains: ['arbitrum'],
    status: 'live',
    costs: 'gas-only',
    effort: 'medium',
    requirements: ['A funded wallet on Arbitrum'],
    steps: [
      'Check current STIP/incentive rounds on the official Arbitrum governance forum',
      'Use incentivized protocols you\'d use anyway — incentives change weekly, verify before acting',
    ],
    official_url: 'https://arbitrum.foundation',
    notes: 'Protocol-level incentive rounds, not a chain airdrop.',
  },
];

function loadCatalog() {
  const p = process.env.AIRDROP_CATALOG_PATH;
  if (p) {
    try {
      const rows = JSON.parse(fs.readFileSync(p, 'utf8'));
      if (Array.isArray(rows) && rows.length) return rows;
    } catch (e) { /* fall through to seed — a broken file never blanks the radar */ }
  }
  return SEED_CATALOG;
}

// ── Eligibility mirror (read-only, honest) ───────────────────────────────────

/**
 * Pure. Hints are FACTS about the user's own linked wallet relative to a
 * campaign — never a claim of qualification (only the project decides that).
 */
function eligibilityHints(campaign, walletCtx) {
  if (!walletCtx || !walletCtx.address) return null;
  const hints = [];
  const funded = new Set((walletCtx.chains || [])
    .filter(c => c.readable && (c.total_usd || 0) > 0).map(c => c.key));
  for (const chainKey of campaign.chains || []) {
    if (chainKey === 'testnet') {
      hints.push({ kind: 'ready', text: 'Testnet — free faucet funds; your linked wallet works as-is.' });
    } else if (funded.has(chainKey)) {
      hints.push({ kind: 'ready', text: `Your linked wallet already holds funds on ${chainKey} — gas is covered.` });
    } else {
      hints.push({ kind: 'gap', text: `No readable funds on ${chainKey} in your linked wallet — you would need to bridge gas first.` });
    }
  }
  return hints;
}

// ── Radar assembly ───────────────────────────────────────────────────────────

const PARTICIPATION_NOTE =
  'Guided only: RUNECLAW prepares the context and checklist — you perform and '
  + 'sign every step in your own wallet. Nothing is automated.';
const ANTI_SYBIL_NOTE =
  'One human, one wallet. Automated or multi-wallet "farming" is against most '
  + 'programs\' terms, is actively hunted by sybil filters, and gets activity '
  + 'retroactively disqualified — RUNECLAW will never do it.';

function buildAirdropRadar(catalog, walletCtx) {
  return {
    generated_at: new Date().toISOString(),
    curated_at: CURATED_AT,
    read_only: true,
    participation: PARTICIPATION_NOTE,
    anti_sybil: ANTI_SYBIL_NOTE,
    verify_note: 'Campaigns churn — always confirm details on the official link before acting.',
    wallet_linked: !!(walletCtx && walletCtx.address),
    campaigns: (catalog || []).map(c => ({
      key: c.key,
      name: c.name,
      project_type: c.project_type,
      chains: c.chains,
      status: c.status,
      costs: c.costs,
      effort: c.effort,
      requirements: c.requirements || [],
      steps: c.steps || [],
      official_url: c.official_url,
      notes: c.notes || '',
      hints: eligibilityHints(c, walletCtx),
    })),
  };
}

// Injectable wallet reader so tests never hit RPCs.
let readWalletPortfolio = getWalletPortfolio;
function setWalletReader(fn) { readWalletPortfolio = fn || getWalletPortfolio; }

/** Radar for an anonymous visitor (no wallet context). */
function getPublicAirdropRadar() {
  return buildAirdropRadar(loadCatalog(), null);
}

/** Radar for a logged-in user — adds hints from their own linked wallet. */
async function getUserAirdropRadar(userId) {
  let walletCtx = null;
  try {
    const address = await walletAddressOf(userId);
    if (address) {
      const pf = await readWalletPortfolio(address);
      walletCtx = {
        address,
        chains: (pf && pf.chains ? pf.chains : []).map(c => ({
          key: c.key, readable: c.readable !== false, total_usd: c.total_usd || 0,
        })),
      };
    }
  } catch (e) { /* hints degrade to null — the radar itself never fails on RPC weather */ }
  return buildAirdropRadar(loadCatalog(), walletCtx);
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(airdrops?|testnets?( participation)?|airdrop radar|farm(ing)? airdrops?)\b/i;

async function maybeHandleAirdropChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const r = await getUserAirdropRadar(userId);
    const live = r.campaigns.filter(c => c.status === 'live');
    const lines = r.campaigns.slice(0, 5).map(c =>
      `• <b>${c.name}</b> (${c.status}, ${c.costs}, effort ${c.effort})`
      + (c.hints && c.hints.some(h => h.kind === 'ready') ? ' — ✅ your wallet is ready' : ''));
    return {
      reply_html:
        `🪂 <b>Airdrop &amp; testnet radar</b> — curated ${r.curated_at}, guided-only<br><br>`
        + `${r.campaigns.length} tracked campaigns, ${live.length} live.<br>`
        + lines.join('<br>')
        + `<br><br><i>${ANTI_SYBIL_NOTE}</i>`
        + '<br><i>Checklists and wallet-readiness live in the dashboard Hub — you sign everything yourself.</i>',
      intent: 'airdrops',
    };
  } catch (e) {
    return { reply_html: 'The airdrop radar is refreshing — try again in a moment.', intent: 'airdrops' };
  }
}

module.exports = {
  SEED_CATALOG,
  CURATED_AT,
  loadCatalog,
  eligibilityHints,
  buildAirdropRadar,
  getPublicAirdropRadar,
  getUserAirdropRadar,
  setWalletReader,
  maybeHandleAirdropChat,
};
