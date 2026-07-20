/**
 * Shared live-ticker source (Bitget USDT-M public tickers).
 *
 * One fetch, one 30s cache, used by the alert engine and the RWA radar.
 * Map shape: { BTCUSDT: { price, change, volume } } — change is a percent
 * (Bitget's decimal fraction ×100), volume is 24h quote volume in USDT.
 * ALERTS_TICKERS_URL overrides the source (tests / alternate routing).
 */

const TICKERS_URL = process.env.ALERTS_TICKERS_URL
  || 'https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES';
const TTL_MS = 30_000;

let cache = { at: 0, map: null };

// Injectable fetch (tests / alternate transports). Null restores the default.
let fetchImpl = null;
function setTickerFetcher(fn) { fetchImpl = fn || null; cache = { at: 0, map: null }; }

async function getTickers() {
  if (fetchImpl) return fetchImpl();
  const now = Date.now();
  if (cache.map && now - cache.at < TTL_MS) return cache.map;
  const res = await fetch(TICKERS_URL, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`tickers HTTP ${res.status}`);
  const data = await res.json();
  const map = {};
  for (const t of (data && data.data) || []) {
    const price = parseFloat(t.lastPr);
    if (!t.symbol || !isFinite(price)) continue;
    map[t.symbol] = {
      price,
      change: (parseFloat(t.change24h) || 0) * 100,
      volume: parseFloat(t.usdtVolume ?? t.quoteVolume) || 0,
    };
  }
  cache = { at: now, map };
  return map;
}

module.exports = { getTickers, setTickerFetcher };
