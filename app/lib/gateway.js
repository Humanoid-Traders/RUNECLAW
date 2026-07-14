/**
 * Bot user-gateway client — server-to-server calls from the website to the
 * bot process's /gateway/* endpoints (web chat + manual trades), authenticated
 * with the shared WEB_GATEWAY_SECRET (>=32 chars, same value on both sides).
 *
 * The browser NEVER talks to the gateway directly: routes/chat.js and
 * routes/webtrade.js authenticate the user with JWT, look up their linked
 * telegram_id server-side, and forward here. Modeled on routes/insight.js.
 */

const http = require('http');
const https = require('https');

const BOT_GATEWAY_URL = (process.env.BOT_GATEWAY_URL || 'http://localhost:8080').replace(/\/+$/, '');
const GATEWAY_SECRET = process.env.WEB_GATEWAY_SECRET || '';

function isConfigured() {
  return GATEWAY_SECRET.length >= 32;
}

function requestJSON(method, gwPath, body, timeoutMs = 20000) {
  return new Promise((resolve, reject) => {
    const url = `${BOT_GATEWAY_URL}/gateway${gwPath}`;
    const mod = url.startsWith('https:') ? https : http;
    const payload = body === undefined ? null : JSON.stringify(body);
    const headers = { 'X-Gateway-Secret': GATEWAY_SECRET };
    if (payload) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = Buffer.byteLength(payload);
    }
    const req = mod.request(url, { method, timeout: timeoutMs, headers }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(data || '{}') }); }
        catch (e) { reject(new Error('Invalid JSON from gateway')); }
      });
    });
    req.on('error', reject);
    req.on('timeout', () => { req.destroy(); reject(new Error('Gateway timeout')); });
    if (payload) req.write(payload);
    req.end();
  });
}

// Forward a gateway response to the browser: pass 4xx through verbatim (the
// UI distinguishes chat_admin_only / not_proposer / live_not_enabled / ...),
// collapse 5xx to a 502.
function relay(res, r) {
  if (r.status >= 200 && r.status < 300) return res.json(r.data);
  if (r.status >= 400 && r.status < 500) return res.status(r.status).json(r.data);
  return res.status(502).json({ error: 'Bot gateway error' });
}

module.exports = {
  isConfigured,
  relay,
  postGateway: (p, b, t) => requestJSON('POST', p, b, t),
  getGateway: (p, t) => requestJSON('GET', p, undefined, t),
};
