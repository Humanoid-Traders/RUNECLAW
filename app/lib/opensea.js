'use strict';
/**
 * OpenSea read-only NFT surface (task #343) — market intelligence only.
 *
 * Scope, pinned by tests: READ-ONLY. Wallet NFT holdings and collection
 * floor/volume stats from the OpenSea API v2. No marketplace machinery —
 * no listings, no offers, no fulfillment, no minting, no wallet
 * credentials. (OpenSea's agent skills bundle can execute trades with
 * wallet keys; that crosses the non-custodial line and is deliberately
 * NOT used — this module speaks plain HTTPS to the public data API.)
 *
 * Free-tier OPENSEA_API_KEY (opensea.io/settings/developer). Without a key
 * every surface reports available:false honestly — never a fabricated
 * radar. Floor prices are public market data (like any ticker).
 */

const https = require('https');

const API = 'https://api.opensea.io/api/v2';
const RADAR_TTL_MS = 10 * 60_000;
const TOP_STATS = 6;

let _fetcher = null;          // injectable for tests / degraded runtimes
let _radarCache = null;
let _radarTs = 0;

function setOpenSeaFetcher(fn) { _fetcher = fn; _radarCache = null; _radarTs = 0; }

function configured() {
  return Boolean((process.env.OPENSEA_API_KEY || '').trim());
}

function fetchJson(path) {
  if (_fetcher) return _fetcher(path);
  return new Promise((resolve, reject) => {
    const req = https.get(`${API}${path}`, {
      headers: { Accept: 'application/json',
        'X-API-KEY': (process.env.OPENSEA_API_KEY || '').trim() },
      timeout: 8000,
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve(JSON.parse(d)); } catch (e) { reject(new Error('bad JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('timeout')); });
  });
}

const NOT_CONFIGURED = {
  available: false,
  reason: 'not_configured',
  note: 'Set OPENSEA_API_KEY (free tier at opensea.io/settings/developer) to '
    + 'enable the read-only NFT surface.',
};

/** Trending collections ranked by REAL seven-day volume — never by hype. */
async function getNftRadar() {
  if (!configured() && !_fetcher) return NOT_CONFIGURED;
  const now = Date.now();
  if (_radarCache && now - _radarTs < RADAR_TTL_MS) return _radarCache;
  let cols;
  try {
    const r = await fetchJson('/collections?order_by=seven_day_volume&limit=12');
    cols = Array.isArray(r && r.collections) ? r.collections : [];
  } catch (e) {
    return { available: false, reason: 'unreachable', note: String(e.message || e) };
  }
  const top = cols.slice(0, TOP_STATS);
  const stats = await Promise.allSettled(top.map(c =>
    fetchJson(`/collections/${encodeURIComponent(c.collection)}/stats`)));
  const entries = top.map((c, i) => {
    const s = stats[i].status === 'fulfilled' ? (stats[i].value || {}) : {};
    const total = s.total || {};
    return {
      slug: c.collection,
      name: c.name || c.collection,
      floor_eth: total.floor_price ?? null,
      seven_day_volume: (((s.intervals || []).find(x => x.interval === 'seven_day') || {}).volume) ?? null,
      owners: total.num_owners ?? null,
    };
  });
  const out = {
    available: true,
    ranked_by: 'seven_day_volume (real traded volume, never hype)',
    entries,
    disclaimer: 'Read-only market data from OpenSea. NFTs are highly '
      + 'illiquid and speculative; floor prices can be manipulated by tiny '
      + 'volumes. RUNECLAW never lists, bids, mints or trades NFTs.',
  };
  _radarCache = out;
  _radarTs = now;
  return out;
}

/** Read-only view of a wallet's NFTs on one chain. Public data by address. */
async function getWalletNfts(address, chain = 'ethereum') {
  const addr = String(address || '').toLowerCase();
  if (!/^0x[0-9a-f]{40}$/.test(addr)) return { available: false, reason: 'bad_address' };
  if (!configured() && !_fetcher) return NOT_CONFIGURED;
  try {
    const r = await fetchJson(
      `/chain/${encodeURIComponent(chain)}/account/${addr}/nfts?limit=20`);
    const nfts = Array.isArray(r && r.nfts) ? r.nfts : [];
    return {
      available: true,
      address: addr,
      chain,
      count: nfts.length,
      items: nfts.map(n => ({
        name: n.name || `${n.collection || 'nft'} #${n.identifier}`,
        collection: n.collection || null,
        token_id: n.identifier ?? null,
        image_url: typeof n.image_url === 'string' ? n.image_url : null,
      })),
      note: 'Read-only mirror; first 20 items. RUNECLAW never moves NFTs.',
    };
  } catch (e) {
    return { available: false, reason: 'unreachable', note: String(e.message || e) };
  }
}

const CHAT_RE = /\b(nft ?radar|nfts?\b.*\b(floor|trending|radar)|opensea|floor price)\b/i;

async function maybeHandleNftChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  const radar = await getNftRadar();
  if (!radar.available) {
    return { reply_html: '🖼 <b>NFT radar</b> — unavailable: '
      + (radar.reason === 'not_configured'
        ? 'the operator has not configured an OpenSea API key yet.'
        : 'OpenSea is unreachable right now.') };
  }
  const rows = radar.entries.map(e =>
    `• <b>${String(e.name).slice(0, 40)}</b> — floor ${e.floor_eth ?? '?'} ETH, `
    + `7d vol ${e.seven_day_volume != null ? Math.round(e.seven_day_volume) : '?'} ETH`);
  return { reply_html: ['🖼 <b>NFT radar</b> — top collections by real 7-day volume:',
    ...rows, `<i>${radar.disclaimer}</i>`].join('<br>') };
}

module.exports = {
  getNftRadar, getWalletNfts, maybeHandleNftChat, setOpenSeaFetcher, CHAT_RE,
};
