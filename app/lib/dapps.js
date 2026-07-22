/**
 * dApp connectors hub — a curated, READ-ONLY directory of reputable DeFi/NFT
 * dApps. RUNECLAW is a launchpad here, not a broker: every entry deep-links to
 * the dApp's own official site where the user connects their own wallet and
 * signs their own transactions. RUNECLAW never proxies, routes, or executes
 * anything from this surface (§4: recommendations/links only; live in-site
 * swap/bridge/stake actions are a separate, confirm-gated, non-custodial
 * feature shipped later).
 *
 * The catalog is intentionally curated and small — known, audited protocols
 * only — rather than an open, spoofable list. Pure & deterministic.
 */

'use strict';

// Chain keys align with lib/wallet.js CHAINS + solana.
const CATEGORIES = ['DEX', 'Perps', 'Lending', 'Staking', 'Yield', 'Bridge', 'NFT', 'Names'];

const DAPPS = [
  // DEX
  { id: 'uniswap', name: 'Uniswap', category: 'DEX', emoji: '🦄', url: 'https://app.uniswap.org/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon', 'bnb'], blurb: 'The largest on-chain spot DEX — swap and provide liquidity.' },
  { id: 'curve', name: 'Curve', category: 'DEX', emoji: '🌊', url: 'https://curve.fi/', chains: ['ethereum', 'arbitrum', 'optimism', 'polygon'], blurb: 'Stablecoin & pegged-asset AMM with deep low-slippage pools.' },
  { id: 'aerodrome', name: 'Aerodrome', category: 'DEX', emoji: '✈️', url: 'https://aerodrome.finance/', chains: ['base'], blurb: 'The central liquidity hub on Base.' },
  { id: 'pancakeswap', name: 'PancakeSwap', category: 'DEX', emoji: '🥞', url: 'https://pancakeswap.finance/', chains: ['bnb', 'ethereum', 'base'], blurb: 'Leading DEX on BNB Chain, multi-chain spot & perps.' },
  // Perps
  { id: 'hyperliquid', name: 'Hyperliquid', category: 'Perps', emoji: '⚡', url: 'https://app.hyperliquid.xyz/', chains: ['hyperliquid'], blurb: 'High-performance on-chain perps on its own L1.' },
  { id: 'gmx', name: 'GMX', category: 'Perps', emoji: '📈', url: 'https://app.gmx.io/', chains: ['arbitrum'], blurb: 'Decentralised perpetuals with a liquidity-pool counterparty.' },
  { id: 'dydx', name: 'dYdX', category: 'Perps', emoji: '🔷', url: 'https://dydx.trade/', chains: ['ethereum'], blurb: 'Order-book perpetuals exchange on its own appchain.' },
  // Lending
  { id: 'aave', name: 'Aave', category: 'Lending', emoji: '👻', url: 'https://app.aave.com/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon'], blurb: 'Blue-chip lending market — supply, borrow, earn.' },
  { id: 'compound', name: 'Compound', category: 'Lending', emoji: '🏦', url: 'https://app.compound.finance/', chains: ['ethereum', 'base', 'arbitrum', 'polygon'], blurb: 'Long-standing algorithmic money market.' },
  { id: 'morpho', name: 'Morpho', category: 'Lending', emoji: '🦋', url: 'https://app.morpho.org/', chains: ['ethereum', 'base'], blurb: 'Efficient lending with curated risk vaults.' },
  { id: 'spark', name: 'Spark', category: 'Lending', emoji: '✨', url: 'https://app.spark.fi/', chains: ['ethereum', 'base'], blurb: 'Sky (Maker) lending & the sDAI savings rate.' },
  // Staking
  { id: 'lido', name: 'Lido', category: 'Staking', emoji: '🌸', url: 'https://stake.lido.fi/', chains: ['ethereum'], blurb: 'Liquid-stake ETH for stETH — the largest LST.' },
  { id: 'rocketpool', name: 'Rocket Pool', category: 'Staking', emoji: '🚀', url: 'https://stake.rocketpool.net/', chains: ['ethereum'], blurb: 'Decentralised ETH liquid staking (rETH).' },
  { id: 'etherfi', name: 'ether.fi', category: 'Staking', emoji: '🔑', url: 'https://app.ether.fi/', chains: ['ethereum'], blurb: 'Non-custodial liquid staking & restaking (eETH).' },
  // Yield
  { id: 'pendle', name: 'Pendle', category: 'Yield', emoji: '🧧', url: 'https://app.pendle.finance/', chains: ['ethereum', 'arbitrum', 'base'], blurb: 'Tokenise and trade future yield (PT/YT markets).' },
  { id: 'yearn', name: 'Yearn', category: 'Yield', emoji: '🔵', url: 'https://yearn.fi/', chains: ['ethereum', 'arbitrum', 'optimism'], blurb: 'Automated yield vaults.' },
  { id: 'beefy', name: 'Beefy', category: 'Yield', emoji: '🐄', url: 'https://app.beefy.com/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon', 'bnb'], blurb: 'Multi-chain auto-compounding yield optimiser.' },
  // Bridge
  { id: 'across', name: 'Across', category: 'Bridge', emoji: '🌉', url: 'https://app.across.to/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon'], blurb: 'Fast, intents-based canonical bridging.' },
  { id: 'stargate', name: 'Stargate', category: 'Bridge', emoji: '⭐', url: 'https://stargate.finance/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon', 'bnb'], blurb: 'LayerZero-powered cross-chain liquidity transport.' },
  { id: 'debridge', name: 'deBridge', category: 'Bridge', emoji: '🔗', url: 'https://app.debridge.finance/', chains: ['ethereum', 'arbitrum', 'optimism', 'polygon', 'bnb'], blurb: 'Cross-chain value transfer & messaging.' },
  // NFT
  { id: 'opensea', name: 'OpenSea', category: 'NFT', emoji: '⛵', url: 'https://opensea.io/', chains: ['ethereum', 'base', 'arbitrum', 'optimism', 'polygon'], blurb: 'The broadest NFT marketplace.' },
  { id: 'blur', name: 'Blur', category: 'NFT', emoji: '🌀', url: 'https://blur.io/', chains: ['ethereum'], blurb: 'Pro NFT marketplace & aggregator.' },
  { id: 'magiceden', name: 'Magic Eden', category: 'NFT', emoji: '🪄', url: 'https://magiceden.io/', chains: ['ethereum', 'base', 'polygon'], blurb: 'Multi-chain NFT marketplace.' },
  // Names
  { id: 'ens', name: 'ENS', category: 'Names', emoji: '🏷️', url: 'https://app.ens.domains/', chains: ['ethereum'], blurb: 'Register and manage your .eth name & profile.' },
];

const CHAIN_LABEL = {
  ethereum: 'Ethereum', base: 'Base', arbitrum: 'Arbitrum', optimism: 'Optimism',
  polygon: 'Polygon', bnb: 'BNB Chain', hyperliquid: 'Hyperliquid', solana: 'Solana',
};

function chains() {
  const seen = new Set();
  for (const d of DAPPS) for (const c of d.chains) seen.add(c);
  return [...seen].map(key => ({ key, label: CHAIN_LABEL[key] || key }));
}

/**
 * List dApps, optionally filtered by category and/or chain (case-insensitive).
 * Unknown filters simply yield an empty list rather than throwing.
 */
function listDapps(opts = {}) {
  const cat = opts.category ? String(opts.category).toLowerCase() : null;
  const chain = opts.chain ? String(opts.chain).toLowerCase() : null;
  return DAPPS.filter(d =>
    (!cat || d.category.toLowerCase() === cat) &&
    (!chain || d.chains.some(c => c.toLowerCase() === chain))
  );
}

const NOTE =
  'Curated directory — RUNECLAW links out to each dApp\'s official site where you connect ' +
  'your own wallet and sign your own transactions. RUNECLAW never routes or executes these; ' +
  'always verify the URL before connecting.';

module.exports = { DAPPS, CATEGORIES, CHAIN_LABEL, chains, listDapps, NOTE };
