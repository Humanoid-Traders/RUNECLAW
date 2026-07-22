/**
 * ENS identity — resolve a wallet's ENS primary name and avatar, READ-ONLY.
 *
 * Reverse-resolves an address to its ENS name (`lookupAddress`) and, if set,
 * the on-chain avatar (`getAvatar`). Read calls only against an Ethereum RPC —
 * this module can never sign or write anything. Every failure is soft: a name
 * that doesn't resolve simply returns null, and the address (shortened) is
 * always the honest fallback.
 *
 * Provider construction mirrors lib/wallet.js (ethers JsonRpcProvider on the
 * ethereum RPC, overridable via WEB3_RPC_URL) and exposes a factory seam so
 * tests can inject a fake provider without network.
 */

'use strict';

const CACHE_MS = 5 * 60_000; // ENS records change rarely.
const RPC_DEFAULT = 'https://cloudflare-eth.com';

let _providerFactory = null;
function setEnsProviderFactory(fn) { _providerFactory = fn; _cache.clear(); }

let _cachedProvider = null;
function provider() {
  if (_providerFactory) return _providerFactory();
  if (!_cachedProvider) {
    const { ethers } = require('ethers');
    _cachedProvider = new ethers.JsonRpcProvider(process.env.WEB3_RPC_URL || RPC_DEFAULT);
  }
  return _cachedProvider;
}

function isAddress(addr) {
  try { const { ethers } = require('ethers'); return ethers.isAddress(addr); }
  catch { return /^0x[a-fA-F0-9]{40}$/.test(String(addr || '')); }
}

function shorten(addr) {
  const a = String(addr || '');
  return a.length >= 10 ? `${a.slice(0, 6)}…${a.slice(-4)}` : a;
}

const _cache = new Map(); // addr(lower) -> { at, identity }

/**
 * Resolve { address, short, ens, avatar, resolved } for an address. `resolved`
 * is false when the RPC lookup itself failed (so the UI can distinguish "no ENS
 * set" from "couldn't check"). Never throws.
 */
async function resolveIdentity(address) {
  if (!isAddress(address)) {
    return { address: null, short: null, ens: null, avatar: null, resolved: false };
  }
  const key = String(address).toLowerCase();
  const hit = _cache.get(key);
  if (hit && (Date.now() - hit.at) < CACHE_MS) return hit.identity;

  const base = { address, short: shorten(address), ens: null, avatar: null, resolved: false };
  try {
    const p = provider();
    // Let a genuine RPC/network error propagate to the outer catch (→
    // resolved:false); ethers returns null (not throws) when there's simply no
    // reverse record, which correctly reads as resolved-with-no-name.
    const name = await p.lookupAddress(address);
    let avatar = null;
    if (name) avatar = await p.getAvatar(name).catch(() => null);
    const identity = { ...base, ens: name || null, avatar: avatar || null, resolved: true };
    _cache.set(key, { at: Date.now(), identity });
    return identity;
  } catch {
    // RPC hiccup — return the honest address-only fallback, do NOT cache.
    return base;
  }
}

module.exports = { resolveIdentity, setEnsProviderFactory, shorten, isAddress };
