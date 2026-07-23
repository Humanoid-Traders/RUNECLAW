/**
 * Market data proxy — fetches live data from Bitget public APIs
 * so the dashboard can display real-time prices without CORS issues.
 */

const express = require('express');
const https = require('https');

const router = express.Router();

// RC-AUD-028(a): in-process per-IP sliding-window rate limit.
// /api/market/* is an unauthenticated outbound-fetch proxy to Bitget; the 5s
// cache alone does not bound per-client request volume. Mirrors the per-IP
// limiter pattern in app/auth.js. Limits are generous so the dashboard's normal
// polling (a handful of endpoints every few seconds) is unaffected.
const marketHits = new Map(); // ip -> number[] (request timestamps in window)
const MARKET_WINDOW_MS = 60 * 1000; // 1 min
const MARKET_MAX = 120; // max requests per IP per window

function pruneMarketHits() {
  const cutoff = Date.now() - MARKET_WINDOW_MS;
  for (const [ip, hits] of marketHits) {
    const recent = hits.filter(ts => ts > cutoff);
    if (recent.length === 0) marketHits.delete(ip);
    else marketHits.set(ip, recent);
  }
  // Cap map size to prevent unbounded growth under IP churn
  if (marketHits.size > 10000) {
    const keys = [...marketHits.keys()];
    for (let i = 0; i < keys.length - 5000; i++) marketHits.delete(keys[i]);
  }
}
const _marketPruneTimer = setInterval(pruneMarketHits, 60000);
if (_marketPruneTimer.unref) _marketPruneTimer.unref();

router.use((req, res, next) => {
  const ip = req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
  const now = Date.now();
  const cutoff = now - MARKET_WINDOW_MS;
  const hits = (marketHits.get(ip) || []).filter(ts => ts > cutoff);
  if (hits.length >= MARKET_MAX) {
    marketHits.set(ip, hits);
    return res.status(429).json({ error: 'Too many requests' });
  }
  hits.push(now);
  marketHits.set(ip, hits);
  next();
});

// Simple HTTPS GET with promise
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, { timeout: 8000 }, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try { resolve(JSON.parse(body)); }
        catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

// Cache to avoid hammering Bitget (5-second TTL)
const cache = {};
function cached(key, ttlMs, fetcher) {
  return async () => {
    const now = Date.now();
    if (cache[key] && now - cache[key].ts < ttlMs) return cache[key].data;
    const data = await fetcher();
    cache[key] = { data, ts: now };
    return data;
  };
}

// Symbol validation — prevent query-param injection
function validateSymbol(sym) {
  return /^[A-Z0-9]{1,20}$/.test(sym);
}

// GET /api/market/tickers - All futures tickers
router.get('/tickers', async (req, res) => {
  try {
    const data = await cached('tickers', 5000, () =>
      fetchJSON('https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES')
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch tickers' });
  }
});

// GET /api/market/ticker/:symbol - Single futures ticker
router.get('/ticker/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const data = await cached(`ticker_${sym}`, 3000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/ticker?symbol=${sym}&productType=USDT-FUTURES`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch ticker' });
  }
});

// Bitget wraps errors in HTTP 200 ({code:"400171",...}). Surface them as 502
// so panels show a Retry instead of a false "no data" empty state.
function relayBitget(res, data) {
  if (data && data.code && data.code !== '00000') {
    return res.status(502).json({ error: `Bitget: ${data.msg || data.code}` });
  }
  res.json(data);
}

// GET /api/market/depth/:symbol - Order book (top 5 levels)
router.get('/depth/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    // merge-depth no longer accepts precision=price (40020) — omit it and let
    // Bitget use the symbol's native price scale.
    const data = await cached(`depth_${sym}`, 5000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/merge-depth?symbol=${sym}&productType=USDT-FUTURES&limit=5`)
    )();
    relayBitget(res, data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch depth' });
  }
});

// GET /api/market/candles/:symbol - Recent 1H candles for VWAP
router.get('/candles/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const gran = req.query.granularity || '1h';
    if (!/^(1min|5min|15min|30min|1h|2h|4h|6h|12h|1d|1w)$/i.test(gran)) return res.status(400).json({ error: 'Invalid granularity' });
    // Bitget now requires uppercase hour+ granularities (1H/4H/1D/1W) and
    // short minute tokens (1m/5m/...) — lowercase "1h" is rejected with
    // 400171 inside an HTTP 200, which the chart used to render as
    // "no candle data".
    const BITGET_GRAN = {
      '1min': '1m', '5min': '5m', '15min': '15m', '30min': '30m',
      '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
      '1d': '1D', '1w': '1W',
    };
    const bg = BITGET_GRAN[gran.toLowerCase()] || gran;
    const limit = Math.min(parseInt(req.query.limit) || 24, 200);
    // Optional ms-epoch window (trade replay theater fetches the candles
    // around a recorded trade). Validated numeric; Bitget ignores unknowns.
    const startTime = /^\d{10,16}$/.test(String(req.query.startTime || '')) ? `&startTime=${req.query.startTime}` : '';
    const endTime = /^\d{10,16}$/.test(String(req.query.endTime || '')) ? `&endTime=${req.query.endTime}` : '';
    const data = await cached(`candles_${sym}_${bg}_${startTime}_${endTime}`, 15000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/candles?symbol=${sym}&productType=USDT-FUTURES&granularity=${bg}&limit=${limit}${startTime}${endTime}`)
    )();
    relayBitget(res, data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch candles' });
  }
});

// GET /api/market/funding/:symbol - Current funding rate
router.get('/funding/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const data = await cached(`funding_${sym}`, 30000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=${sym}&productType=USDT-FUTURES`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch funding' });
  }
});

// GET /api/market/dex — DEX↔CEX comparison (Hyperliquid mids vs this venue's
// perp prices; public info API, read-only).
router.get('/dex', async (req, res) => {
  try {
    const cmp = await require('../lib/dex').getDexCompare();
    res.setHeader('Cache-Control', 'public, max-age=30');
    res.json(cmp);
  } catch (err) {
    res.status(502).json({ error: 'DEX comparison unavailable' });
  }
});

// GET /api/market/rwa — RWA & on-chain radar (read-only market intelligence
// from the live ticker map; curated universe filtered to actual listings).
router.get('/rwa', async (req, res) => {
  try {
    const radar = await require('../lib/rwa').getRadar();
    res.setHeader('Cache-Control', 'public, max-age=30');
    res.json(radar);
  } catch (err) {
    res.status(502).json({ error: 'RWA radar unavailable' });
  }
});

// GET /api/market/meme — Meme & AI-agent token radar (read-only DEXScreener
// intelligence with an explicit per-token safety read; never trades/launches).
router.get('/meme', async (req, res) => {
  try {
    const radar = await require('../lib/meme').getRadar();
    res.setHeader('Cache-Control', 'public, max-age=30');
    res.json(radar);
  } catch (err) {
    res.status(502).json({ error: 'Meme radar unavailable' });
  }
});

// GET /api/market/venue-router — per-pair cheapest-venue funding read.
// Recommendations only; RUNECLAW never auto-routes orders.
router.get('/venue-router', async (req, res) => {
  try {
    const table = await require('../lib/venue_router').getVenueRouter();
    res.setHeader('Cache-Control', 'public, max-age=60');
    res.json(table);
  } catch (err) {
    res.status(502).json({ error: 'Venue router unavailable' });
  }
});

// GET /api/market/onchain-flow — 24h DEX taker-flow radar for the majors
// (keyless, read-only; explicitly NOT exchange netflow — the payload says so).
router.get('/onchain-flow', async (req, res) => {
  try {
    const radar = await require('../lib/onchain_flow').getFlowRadar();
    res.setHeader('Cache-Control', 'public, max-age=60');
    res.json(radar);
  } catch (err) {
    res.status(502).json({ error: 'Flow radar unavailable' });
  }
});

// GET /api/market/strengthmap — factor scoring of the whole USDT-perp universe
// from PUBLIC Bitget market data (price/24h/volume/funding/OI), for the 3D
// Strength Map. Read-only, no account data, no user P&L — pure market viz (§4).
// A rolling OI snapshot lets it show ΔOI between polls.
const { buildStrengthMap } = require('../lib/strengthmap');
let _oiSnapshot = null; // { [symbol]: oi_usd } from the previous build
router.get('/strengthmap', async (req, res) => {
  try {
    const limit = Math.max(20, Math.min(400, parseInt(req.query.limit, 10) || 220));
    const raw = await cached('tickers', 5000, () =>
      fetchJSON('https://api.bitget.com/api/v2/mix/market/tickers?productType=USDT-FUTURES')
    )();
    const tickers = (raw && Array.isArray(raw.data)) ? raw.data : [];
    if (!tickers.length) return res.status(502).json({ error: 'Market data unavailable' });
    const { coins, oiSnapshot, count } = buildStrengthMap(tickers, _oiSnapshot, limit);
    _oiSnapshot = oiSnapshot; // remember for the next poll's ΔOI
    res.setHeader('Cache-Control', 'public, max-age=15');
    res.json({ coins, count, at: new Date().toISOString() });
  } catch (err) {
    res.status(502).json({ error: 'Strength map unavailable' });
  }
});

// GET /api/market/venues/:base — CEX + DEX venues where a coin is tradeable, as
// deep links, for the Strength Map's "open the trade" picker. Recommendations
// only; RUNECLAW never auto-routes an order.
const { venuesFor } = require('../lib/venue_links');
router.get('/venues/:base', (req, res) => {
  const venues = venuesFor(req.params.base);
  if (!venues.length) return res.status(400).json({ error: 'Invalid symbol' });
  res.setHeader('Cache-Control', 'public, max-age=3600');
  res.json({ base: String(req.params.base).toUpperCase().replace(/USDT$/, ''), venues });
});

module.exports = router;
