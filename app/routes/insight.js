/**
 * Market insight proxy — forwards to the bot's API bridge `/insight/{symbol}`
 * so the dashboard can render the SAME decision picture the bot trades off
 * (scored S/R levels, FVGs, liquidity pools, confluence votes, tape CVD and
 * gate telemetry) without exposing the bridge directly. Public market data,
 * same trust level as /api/market/* — no auth required.
 */

const express = require('express');
const http = require('http');
const https = require('https');

const router = express.Router();

// Bot API bridge base URL (uvicorn api_bridge:app, :8000 in docker-compose).
const BOT_API_URL = (process.env.BOT_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

// In-process per-IP sliding-window rate limit — mirrors the limiter pattern in
// routes/market.js. The bridge itself enforces 30 req/min per client IP, so we
// stay comfortably below that with the cache and keep abusive clients out.
const insightHits = new Map(); // ip -> number[] (request timestamps in window)
const INSIGHT_WINDOW_MS = 60 * 1000; // 1 min
const INSIGHT_MAX = 60; // max requests per IP per window

function pruneInsightHits() {
  const cutoff = Date.now() - INSIGHT_WINDOW_MS;
  for (const [ip, hits] of insightHits) {
    const recent = hits.filter(ts => ts > cutoff);
    if (recent.length === 0) insightHits.delete(ip);
    else insightHits.set(ip, recent);
  }
  // Cap map size to prevent unbounded growth under IP churn
  if (insightHits.size > 10000) {
    const keys = [...insightHits.keys()];
    for (let i = 0; i < keys.length - 5000; i++) insightHits.delete(keys[i]);
  }
}
const _insightPruneTimer = setInterval(pruneInsightHits, 60000);
if (_insightPruneTimer.unref) _insightPruneTimer.unref();

router.use((req, res, next) => {
  const ip = req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
  const now = Date.now();
  const cutoff = now - INSIGHT_WINDOW_MS;
  const hits = (insightHits.get(ip) || []).filter(ts => ts > cutoff);
  if (hits.length >= INSIGHT_MAX) {
    insightHits.set(ip, hits);
    return res.status(429).json({ error: 'Too many requests' });
  }
  hits.push(now);
  insightHits.set(ip, hits);
  next();
});

// Simple GET with promise (http or https depending on the bridge URL)
function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https:') ? https : http;
    const req = mod.get(url, { timeout: 12000 }, (res) => {
      let body = '';
      res.on('data', d => body += d);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(body) }); }
        catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Timeout')); });
  });
}

// Cache to avoid hammering the bridge (30-second TTL; overlays refresh ~60s)
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

// Symbol validation — mirrors the bridge's _SYMBOL_RE (accepts "BTC/USDT");
// prevents path/query injection before the value reaches the upstream URL.
function validateSymbol(sym) {
  return /^[A-Z0-9]{1,15}(\/[A-Z0-9]{1,15})?$/.test(sym);
}

// GET /api/insight/:symbol?timeframe=1h&limit=200 - Bot decision picture
// (:symbol contains a slash — the dashboard sends it encodeURIComponent'd,
// Express decodes it back into req.params.symbol.)
router.get('/:symbol', async (req, res) => {
  try {
    const sym = req.params.symbol.toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const tf = req.query.timeframe || '1h';
    if (!/^(1m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)$/.test(tf)) return res.status(400).json({ error: 'Invalid timeframe' });
    const limit = Math.min(parseInt(req.query.limit) || 200, 500);
    const r = await cached(`insight_${sym}_${tf}_${limit}`, 30000, () =>
      fetchJSON(`${BOT_API_URL}/insight/${encodeURIComponent(sym)}?timeframe=${tf}&limit=${limit}`)
    )();
    if (r.status !== 200) {
      const detail = r.data && r.data.detail;
      return res.status(r.status >= 500 ? 502 : r.status).json({ error: detail || 'Insight unavailable' });
    }
    res.json(r.data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch insight' });
  }
});

module.exports = router;
