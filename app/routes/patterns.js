/**
 * Chart-pattern proxy — forwards to the bot's API bridge `/patterns/{symbol}`
 * so the dashboard's Deep Scan view can look up the live chart + candlestick
 * pattern read for ANY symbol on demand (name + bullish/bearish signal +
 * confidence). This is the same detector output behind the Telegram /deepscan
 * card; the batch readout rides the scan sync, this is the drill-down.
 *
 * Public market data, same trust level as /api/insight and /api/market/* — no
 * auth required. Degrades honestly (502) when the bridge is unreachable.
 */

const express = require('express');
const http = require('http');
const https = require('https');

const router = express.Router();

const BOT_API_URL = (process.env.BOT_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

// Per-IP sliding-window rate limit (mirrors routes/insight.js).
const hitsByIp = new Map();
const WINDOW_MS = 60 * 1000;
const MAX = 60;

function prune() {
  const cutoff = Date.now() - WINDOW_MS;
  for (const [ip, hits] of hitsByIp) {
    const recent = hits.filter(ts => ts > cutoff);
    if (recent.length === 0) hitsByIp.delete(ip);
    else hitsByIp.set(ip, recent);
  }
  if (hitsByIp.size > 10000) {
    const keys = [...hitsByIp.keys()];
    for (let i = 0; i < keys.length - 5000; i++) hitsByIp.delete(keys[i]);
  }
}
const _pruneTimer = setInterval(prune, 60000);
if (_pruneTimer.unref) _pruneTimer.unref();

router.use((req, res, next) => {
  const ip = req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
  const now = Date.now();
  const cutoff = now - WINDOW_MS;
  const hits = (hitsByIp.get(ip) || []).filter(ts => ts > cutoff);
  if (hits.length >= MAX) {
    hitsByIp.set(ip, hits);
    return res.status(429).json({ error: 'Too many requests' });
  }
  hits.push(now);
  hitsByIp.set(ip, hits);
  next();
});

function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const mod = url.startsWith('https:') ? https : http;
    const r = mod.get(url, { timeout: 12000 }, (resp) => {
      let body = '';
      resp.on('data', d => body += d);
      resp.on('end', () => {
        try { resolve({ status: resp.statusCode, data: JSON.parse(body) }); }
        catch (e) { reject(new Error('Invalid JSON')); }
      });
    });
    r.on('error', reject);
    r.on('timeout', () => { r.destroy(); reject(new Error('Timeout')); });
  });
}

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

function validateSymbol(sym) {
  return /^[A-Z0-9]{1,15}(\/[A-Z0-9]{1,15})?$/.test(sym);
}

// GET /api/patterns?symbol=BTC/USDT&timeframe=4h — live chart+candle patterns.
async function handler(req, res, rawSym) {
  try {
    const sym = String(rawSym || '').toUpperCase();
    if (!validateSymbol(sym)) return res.status(400).json({ error: 'Invalid symbol' });
    const tf = req.query.timeframe || '4h';
    if (!/^(1m|5m|15m|30m|1h|2h|4h|6h|12h|1d|1w)$/.test(tf)) {
      return res.status(400).json({ error: 'Invalid timeframe' });
    }
    const r = await cached(`patterns_${sym}_${tf}`, 45000, () =>
      fetchJSON(`${BOT_API_URL}/patterns/${encodeURIComponent(sym)}?timeframe=${tf}`)
    )();
    if (r.status !== 200) {
      const detail = r.data && r.data.detail;
      return res.status(r.status >= 500 ? 502 : r.status).json({ error: detail || 'Patterns unavailable' });
    }
    res.json(r.data);
  } catch (err) {
    res.status(502).json({ error: 'Failed to fetch patterns' });
  }
}

router.get('/', (req, res) => handler(req, res, req.query.symbol));
router.get('/:symbol', (req, res) => handler(req, res, req.params.symbol));

module.exports = router;
