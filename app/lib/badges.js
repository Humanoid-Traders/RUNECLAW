/**
 * Wallet-native on-chain badges — a small, honest reputation surface derived
 * ONLY from what a wallet verifiably holds right now (ENS name, metaverse LAND,
 * NFT count, chains in use, DeFi-token holdings). Every badge is earned from a
 * real, checkable fact — no self-reported claims, no history/dollar bragging on
 * a shareable surface (§4). Unearned badges are returned too (locked) so the
 * user can see what's left to earn.
 *
 * Pure & deterministic — unit-testable object-in / list-out.
 */

'use strict';

// Tokens that mark an address as an active DeFi user (governance / LSTs /
// blue-chip protocol tokens). Matched case-insensitively against held symbols.
const DEFI_TOKENS = new Set(['AAVE', 'LINK', 'PENDLE', 'ONDO', 'ARB', 'OP', 'LDO', 'UNI', 'CRV', 'GMX', 'MORPHO', 'STETH', 'WSTETH', 'RETH', 'EETH', 'CBETH']);

/**
 * @param {object} ctx
 *   ens            {string|null} — the wallet's ENS primary name.
 *   landKinds      {string[]}    — metaverse-item kinds held (e.g. ['land']).
 *   nftCount       {number}      — total NFTs held (best-effort count).
 *   chainsWithBalance {number}   — how many chains hold >$0.
 *   assetSymbols   {string[]}    — token symbols held on-chain.
 */
function computeBadges(ctx = {}) {
  const ens = ctx.ens || null;
  const landKinds = Array.isArray(ctx.landKinds) ? ctx.landKinds : [];
  const nftCount = Number(ctx.nftCount) || 0;
  const chains = Number(ctx.chainsWithBalance) || 0;
  const held = new Set((Array.isArray(ctx.assetSymbols) ? ctx.assetSymbols : []).map(s => String(s || '').toUpperCase()));
  const defiHits = [...held].filter(s => DEFI_TOKENS.has(s));

  const defs = [
    { key: 'ens', emoji: '🏷️', label: 'ENS Identity', earned: !!ens, detail: ens ? `Named ${ens}` : 'Set an ENS primary name to earn this.' },
    { key: 'landholder', emoji: '🗺️', label: 'Metaverse Landholder', earned: landKinds.includes('land'), detail: landKinds.includes('land') ? 'Holds metaverse LAND.' : 'Own a parcel in a supported world.' },
    { key: 'collector', emoji: '🖼️', label: 'NFT Collector', earned: nftCount >= 5, detail: nftCount >= 5 ? `Holds ${nftCount}+ NFTs.` : `Holds ${nftCount} NFTs — 5+ earns this.` },
    { key: 'multichain', emoji: '⛓️', label: 'Multi-chain', earned: chains >= 2, detail: chains >= 2 ? `Active on ${chains} chains.` : 'Hold a balance on 2+ chains.' },
    { key: 'defi_native', emoji: '🏦', label: 'DeFi Native', earned: defiHits.length > 0, detail: defiHits.length ? `Holds ${defiHits.slice(0, 4).join(', ')}.` : 'Hold a blue-chip DeFi / LST token.' },
  ];

  const earned = defs.filter(b => b.earned).length;
  return { badges: defs, earned, total: defs.length };
}

module.exports = { computeBadges, DEFI_TOKENS };
