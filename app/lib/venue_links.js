'use strict';
/**
 * Where can I trade this coin? For a base ticker (BTC, SOL, …) return the CEX +
 * DEX venues where it's tradeable, as deep links, so the Strength Map's detail
 * panel lets a user pick a platform and open the trade there. Recommendations
 * only — RUNECLAW never auto-routes an order (§4). `runeclaw:true` marks venues
 * RUNECLAW can also execute on from inside the app.
 *
 * Links are best-effort deep links to each venue's pair/search page; if a given
 * pair doesn't exist on a venue the venue shows its own not-found. Pure so the
 * route/UI can call it and tests can pin the output.
 */

// Base is validated to a plain ticker before any interpolation.
const BASE_RE = /^[A-Z0-9]{1,20}$/;

function venuesFor(base) {
  const b = String(base || '').toUpperCase().replace(/USDT$/, '');
  if (!BASE_RE.test(b)) return [];
  const lc = b.toLowerCase();
  return [
    // CEX perps (USDT-M). Bitget is the Strength Map's own data source.
    { id: 'bitget', name: 'Bitget', type: 'CEX', kind: 'perp', runeclaw: true,
      url: `https://www.bitget.com/futures/usdt/${b}USDT` },
    { id: 'bybit', name: 'Bybit', type: 'CEX', kind: 'perp', runeclaw: true,
      url: `https://www.bybit.com/trade/usdt/${b}USDT` },
    { id: 'bingx', name: 'BingX', type: 'CEX', kind: 'perp', runeclaw: true,
      url: `https://bingx.com/en/perpetual/${b}-USDT` },
    { id: 'okx', name: 'OKX', type: 'CEX', kind: 'perp', runeclaw: false,
      url: `https://www.okx.com/trade-swap/${lc}-usdt-swap` },
    // DEX perps + on-chain spot discovery.
    { id: 'hyperliquid', name: 'Hyperliquid', type: 'DEX', kind: 'perp', runeclaw: true,
      url: `https://app.hyperliquid.xyz/trade/${b}` },
    { id: 'dexscreener', name: 'DexScreener', type: 'DEX', kind: 'spot', runeclaw: false,
      url: `https://dexscreener.com/search?q=${encodeURIComponent(b)}` },
  ];
}

module.exports = { venuesFor, BASE_RE };
