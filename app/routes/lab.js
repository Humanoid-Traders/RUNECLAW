/**
 * Strategy Lab proxy — forwards to the bot bridge's /lab/* endpoints so
 * logged-in users can run bounded backtests on the frozen benchmark
 * snapshots from the dashboard. The bridge enforces the hard limits (one
 * job at a time, whitelisted datasets/symbols, clamped params, subprocess
 * timeout); this layer adds login + a per-IP rate limit so anonymous
 * traffic can't occupy the single job slot.
 */

const express = require('express');
const http = require('http');
const https = require('https');
const { authMiddleware } = require('../auth');

const router = express.Router();

const BOT_API_URL = (process.env.BOT_API_URL || 'http://localhost:8000').replace(/\/+$/, '');

router.use(authMiddleware);

// Light per-IP limiter: the Lab is polled every few seconds while a job
// runs, so allow bursts but stop hammering.
const hits = new Map();
router.use((req, res, next) => {
  const ip = req.ip || 'unknown';
  const now = Date.now();
  const recent = (hits.get(ip) || []).filter(t => t > now - 60000);
  if (recent.length >= 60) return res.status(429).json({ error: 'Too many requests' });
  recent.push(now);
  hits.set(ip, recent);
  if (hits.size > 5000) hits.clear();
  next();
});

function relay(method, path, body) {
  return new Promise((resolve) => {
    const url = `${BOT_API_URL}${path}`;
    const mod = url.startsWith('https:') ? https : http;
    const payload = body ? JSON.stringify(body) : null;
    const req = mod.request(url, {
      method,
      timeout: 15000,
      headers: payload ? { 'Content-Type': 'application/json' } : {},
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(d) }); }
        catch (e) { resolve({ status: 502, data: { error: 'Bad bridge response' } }); }
      });
    });
    req.on('timeout', () => { req.destroy(); resolve({ status: 504, data: { error: 'Bridge timeout' } }); });
    req.on('error', () => resolve({
      status: 503,
      data: { error: 'lab_bridge_offline', detail: 'The Strategy Lab needs the bot analysis bridge (api_bridge.py on the bot host).' },
    }));
    if (payload) req.write(payload);
    req.end();
  });
}

router.get('/meta', async (req, res) => {
  const r = await relay('GET', '/lab/meta');
  res.status(r.status).json(r.data);
});

router.post('/run', async (req, res) => {
  const b = req.body || {};
  // Shape-check here so garbage never reaches the bridge; the bridge
  // re-validates against the actual snapshot manifests.
  const body = {
    dataset: String(b.dataset || ''),
    symbols: Array.isArray(b.symbols) ? b.symbols.slice(0, 4).map(String) : [],
    last_bars: parseInt(b.last_bars, 10) || 1500,
    confidence_threshold: parseFloat(b.confidence_threshold) || 0,
    balance: parseFloat(b.balance) || 10000,
  };
  // Optional preset entry gates (marketplace "Reproduce in Lab"). Forwarded only
  // when present; the bridge validates/clamps them. Omitted -> a normal run.
  if (b.volume_spike_min != null && b.volume_spike_min !== '') {
    body.volume_spike_min = parseFloat(b.volume_spike_min);
  }
  if (b.regime_filter) body.regime_filter = String(b.regime_filter).slice(0, 20);
  if (b.rsi_max != null && b.rsi_max !== '') body.rsi_max = parseFloat(b.rsi_max);
  const r = await relay('POST', '/lab/run', body);
  res.status(r.status).json(r.data);
});

router.get('/status/:id', async (req, res) => {
  if (!/^[a-f0-9]{6,32}$/.test(req.params.id)) {
    return res.status(400).json({ error: 'Bad job id' });
  }
  const r = await relay('GET', `/lab/status/${req.params.id}`);
  res.status(r.status).json(r.data);
});

module.exports = router;
