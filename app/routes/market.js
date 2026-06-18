/**
 * Market data proxy — fetches live data from Bitget public APIs
 * so the dashboard can display real-time prices without CORS issues.
 */

const express = require('express');
const https = require('https');

const router = express.Router();

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
    const data = await cached(`ticker_${sym}`, 3000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/ticker?symbol=${sym}&productType=USDT-FUTURES`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch ticker' });
  }
});

// GET /api/market/depth/:symbol - Order book (top 5 levels)
router.get('/depth/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    const data = await cached(`depth_${sym}`, 5000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/merge-depth?symbol=${sym}&productType=USDT-FUTURES&precision=price&limit=5`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch depth' });
  }
});

// GET /api/market/candles/:symbol - Recent 1H candles for VWAP
router.get('/candles/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    const gran = req.query.granularity || '1h';
    const limit = Math.min(parseInt(req.query.limit) || 24, 200);
    const data = await cached(`candles_${sym}_${gran}`, 15000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/candles?symbol=${sym}&productType=USDT-FUTURES&granularity=${gran}&limit=${limit}`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch candles' });
  }
});

// GET /api/market/funding/:symbol - Current funding rate
router.get('/funding/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    const data = await cached(`funding_${sym}`, 30000, () =>
      fetchJSON(`https://api.bitget.com/api/v2/mix/market/current-fund-rate?symbol=${sym}&productType=USDT-FUTURES`)
    )();
    res.json(data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch funding' });
  }
});

module.exports = router;
