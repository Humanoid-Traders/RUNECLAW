/**
 * DEX surface — Hyperliquid, read-only.
 *
 * RUNECLAW already carries a Hyperliquid venue adapter (operator /venue);
 * this makes the DEX visible to every user: live Hyperliquid mid prices for
 * the majors, side by side with this venue's perp prices, as a DEX↔CEX
 * basis read. Public info API only (no keys, no account, no orders) —
 * non-custodial DEX execution remains design-only pending operator + legal
 * review.
 */

const { getTickers } = require('./tickers');

const HL_INFO_URL = process.env.HL_INFO_URL || 'https://api.hyperliquid.xyz/info';
const TTL_MS = 30_000;

// Majors to compare — present on both venues.
const COMPARE = ['BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'AVAX', 'LINK', 'HYPE'];

function round2(v) { return Math.round(v * 100) / 100; }

let midsCache = { at: 0, mids: null };
async function defaultFetchMids() {
  const now = Date.now();
  if (midsCache.mids && now - midsCache.at < TTL_MS) return midsCache.mids;
  const res = await fetch(HL_INFO_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ type: 'allMids' }),
    signal: AbortSignal.timeout(10_000),
  });
  if (!res.ok) throw new Error(`HL info HTTP ${res.status}`);
  const mids = await res.json();                 // { BTC: "98123.5", ... }
  if (!mids || typeof mids !== 'object') throw new Error('HL info shape');
  midsCache = { at: now, mids };
  return mids;
}
let fetchMids = defaultFetchMids;
function setMidsFetcher(fn) { fetchMids = fn || defaultFetchMids; }

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

/** Pure DEX↔CEX comparison from an HL mids map + a venue ticker map. */
function buildCompare(mids, tickers) {
  const rows = [];
  for (const base of COMPARE) {
    const dex = parseFloat(mids && mids[base]);
    const cex = tickers && tickers[`${base}USDT`] ? tickers[`${base}USDT`].price : null;
    if (!isFinite(dex)) continue;                // not on the DEX → omitted
    const delta_bps = cex && isFinite(cex) && cex > 0
      ? round2((dex - cex) / cex * 10_000) : null;
    rows.push({ base, dex_mid: dex, cex_price: cex, delta_bps });
  }
  const deltas = rows.filter(r => r.delta_bps !== null).map(r => Math.abs(r.delta_bps));
  return {
    read_only: true,
    dex: 'Hyperliquid (on-chain perps)',
    cex: 'Bitget USDT-M perpetuals',
    rows,
    avg_abs_delta_bps: deltas.length
      ? round2(deltas.reduce((a, b) => a + b, 0) / deltas.length) : null,
    execution_note: 'The engine carries a Hyperliquid venue adapter (operator /venue). '
      + 'Non-custodial DEX execution for users is design-only pending review.',
    generated_at: new Date().toISOString(),
  };
}

async function getDexCompare() {
  const [mids, tickers] = await Promise.all([fetchMids(), fetchTickers()]);
  return buildCompare(mids, tickers);
}

module.exports = { COMPARE, buildCompare, getDexCompare, setMidsFetcher, setTickerFetcher };
