/**
 * Macro AI — a read-only macro/market-regime picture for the website.
 *
 * Aggregates three real, public signals into one risk-on/off read plus a plain-
 * language brief:
 *   • Fear & Greed index            (alternative.me)   — crypto sentiment
 *   • Global market structure        (CoinGecko /global) — total cap, 24h change,
 *                                                          BTC/ETH dominance
 *   • The engine's own BTC regime    (synced scan cache) — the bot's live read
 *
 * `assembleMacro()` is a PURE function of those inputs (unit-tested); the route
 * just fetches, caches and hands it the data. Everything degrades honestly: a
 * missing source is omitted and the risk blend re-weights around it. No auth —
 * same trust level as /api/market/* and /api/insight (public market data).
 */

const express = require('express');
const https = require('https');
const gateway = require('../lib/gateway');

const router = express.Router();

// ── Per-IP sliding-window rate limit (mirrors routes/market.js) ──────────────
const hitsByIp = new Map();
const WINDOW_MS = 60 * 1000;
const MAX = 40;
function prune() {
  const cutoff = Date.now() - WINDOW_MS;
  for (const [ip, hits] of hitsByIp) {
    const recent = hits.filter(ts => ts > cutoff);
    if (recent.length === 0) hitsByIp.delete(ip); else hitsByIp.set(ip, recent);
  }
  if (hitsByIp.size > 10000) {
    const keys = [...hitsByIp.keys()];
    for (let i = 0; i < keys.length - 5000; i++) hitsByIp.delete(keys[i]);
  }
}
const _t = setInterval(prune, 60000);
if (_t.unref) _t.unref();
router.use((req, res, next) => {
  const ip = req.ip || (req.socket && req.socket.remoteAddress) || 'unknown';
  const now = Date.now();
  const hits = (hitsByIp.get(ip) || []).filter(ts => ts > now - WINDOW_MS);
  if (hits.length >= MAX) { hitsByIp.set(ip, hits); return res.status(429).json({ error: 'Too many requests' }); }
  hits.push(now); hitsByIp.set(ip, hits);
  next();
});

function fetchJSON(url) {
  return new Promise((resolve, reject) => {
    const r = https.get(url, { timeout: 9000, headers: { 'User-Agent': 'RUNECLAW/1.0' } }, (resp) => {
      let body = '';
      resp.on('data', d => (body += d));
      resp.on('end', () => { try { resolve(JSON.parse(body)); } catch (e) { reject(new Error('Invalid JSON')); } });
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

// ── Pure assembly (unit-tested) ──────────────────────────────────────────────
const num = (v) => { const n = Number(v); return Number.isFinite(n) ? n : null; };
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

function classifyBand(risk) {
  if (risk < 22) return { key: 'risk_off', label: 'Risk-Off', tone: 'down' };
  if (risk < 42) return { key: 'cautious', label: 'Cautious', tone: 'down' };
  if (risk < 58) return { key: 'neutral', label: 'Neutral', tone: '' };
  if (risk < 78) return { key: 'risk_on', label: 'Risk-On', tone: 'up' };
  return { key: 'euphoric', label: 'Euphoric', tone: 'up' };
}

function fmtCap(n) {
  if (n == null) return '—';
  if (n >= 1e12) return '$' + (n / 1e12).toFixed(2) + 'T';
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(n);
}

function buildBrief(m) {
  const s = [];
  if (m.band) {
    s.push(`Market posture reads ${m.band.label.toUpperCase()} (${m.risk_score}/100 on the risk-on scale).`);
  }
  if (m.fear_greed) {
    const fg = m.fear_greed;
    let d = '';
    if (fg.previous != null && fg.value != null) {
      const diff = fg.value - fg.previous;
      d = Math.abs(diff) >= 2 ? ` — ${diff > 0 ? 'up' : 'down'} ${Math.abs(diff)} from yesterday` : ' — flat vs yesterday';
    }
    s.push(`Crypto sentiment is ${fg.classification} at ${fg.value}/100${d}.`);
  }
  if (m.market_cap_usd != null) {
    const dir = m.market_cap_change_24h == null ? '' :
      `, ${m.market_cap_change_24h >= 0 ? 'up' : 'down'} ${Math.abs(m.market_cap_change_24h).toFixed(1)}% over 24h`;
    let dom = '';
    if (m.btc_dominance != null) dom = ` BTC dominance ${m.btc_dominance.toFixed(1)}%${m.eth_dominance != null ? `, ETH ${m.eth_dominance.toFixed(1)}%` : ''}.`;
    s.push(`Total crypto market cap is ${fmtCap(m.market_cap_usd)}${dir}.${dom}`);
  }
  if (m.structure) {
    s.push(`Capital is ${m.structure === 'BTC-led' ? 'concentrated in BTC — a defensive tilt' : m.structure === 'Alt-heavy' ? 'rotating into alts — a risk-seeking tilt' : 'spread broadly across the market'}.`);
  }
  if (m.regime && m.regime.label) {
    const sc = m.regime.score != null ? ` (score ${Number(m.regime.score).toFixed(2)})` : '';
    s.push(`The engine's live BTC regime is ${String(m.regime.label).toUpperCase()}${sc}.`);
  }
  // Event calendar — stated without a live countdown so the brief stays
  // deterministic; the client renders the ticking time-to-event.
  if (m.event && m.event.state && m.event.state !== 'NORMAL') {
    if (m.event.state === 'EVENT_LOCKDOWN' && m.event.active) {
      s.push(`A macro event window is open — ${m.event.active.label} is printing; the engine holds through high-impact prints.`);
    } else if (m.event.next) {
      s.push(`High-impact macro event ahead: ${m.event.next.label}${m.event.state === 'PRE_EVENT_CAUTION' ? ' — the engine is de-risking into it' : ''}.`);
    }
  }
  // Band-specific takeaway.
  const takeaway = {
    risk_off: 'Defensive backdrop — the agent tightens risk, favours majors and cash, and waits for confirmation.',
    cautious: 'Cautious backdrop — smaller size, quicker exits, and only high-conviction setups.',
    neutral: 'Balanced backdrop — no strong tailwind either way; the agent trades setups on their own merit.',
    risk_on: 'Constructive backdrop — trends have room to run, though the agent still respects its risk gates.',
    euphoric: 'Euphoric backdrop — momentum is strong but crowded; the agent trims into strength and guards profits.',
  };
  if (m.band) s.push(takeaway[m.band.key]);
  return s.join(' ');
}

/**
 * Blend the available signals into one macro read. Fear & Greed is the anchor;
 * 24h market-cap momentum and the engine's BTC regime tilt it. Missing inputs
 * are skipped and the weights renormalise, so the score is always defined when
 * at least one source is present.
 */
function assembleMacro({ global, fng, regime, calendar } = {}) {
  const g = global || {};
  const ev = (e) => (e && (e.type || e.label)) ? {
    type: e.type || null, label: e.label || null,
    scheduled_utc: e.scheduled_utc || null, impact: e.impact || null,
  } : null;
  const btcDom = num(g.btc_dom), ethDom = num(g.eth_dom);
  const out = {
    risk_score: null,
    band: null,
    fear_greed: null,
    market_cap_usd: num(g.mcap_usd),
    market_cap_change_24h: num(g.mcap_chg_24h),
    btc_dominance: btcDom,
    eth_dominance: ethDom,
    // Everything that isn't BTC or ETH — the alt share of total cap.
    others_dominance: (btcDom != null && ethDom != null) ? Math.max(0, +(100 - btcDom - ethDom).toFixed(1)) : null,
    // Where the money sits: BTC-led (defensive), Alt-heavy (risk-seeking), or broad.
    structure: btcDom == null ? null : (btcDom >= 55 ? 'BTC-led' : btcDom <= 48 ? 'Alt-heavy' : 'Broad'),
    volume_24h_usd: num(g.vol_usd),
    regime: (regime && (regime.label || regime.score != null)) ? {
      label: regime.label || null,
      score: num(regime.score),
    } : null,
    // Macro event calendar (synced from the bot). The client computes a live
    // countdown from next.scheduled_utc; state drives the event-risk banner.
    event: (calendar && calendar.state) ? {
      state: String(calendar.state),
      stale: !!calendar.stale,
      next: ev(calendar.next_event),
      active: ev(calendar.active_event),
    } : null,
    sources: [],
    brief: '',
  };

  if (fng && fng.value != null) {
    out.fear_greed = {
      value: clamp(num(fng.value) ?? 0, 0, 100),
      classification: String(fng.classification || '').trim() || 'Unknown',
      previous: fng.previous != null ? clamp(num(fng.previous) ?? 0, 0, 100) : null,
    };
    out.sources.push('fear_greed');
  }
  if (out.market_cap_usd != null) out.sources.push('global');
  if (out.regime) out.sources.push('regime');

  const parts = [];
  if (out.fear_greed) parts.push({ w: 0.62, v: out.fear_greed.value });
  if (out.market_cap_change_24h != null) parts.push({ w: 0.20, v: clamp(50 + out.market_cap_change_24h * 6.5, 0, 100) });
  if (out.regime && out.regime.score != null) parts.push({ w: 0.18, v: clamp(50 + out.regime.score * 50, 0, 100) });
  if (parts.length) {
    const wsum = parts.reduce((a, p) => a + p.w, 0);
    out.risk_score = Math.round(parts.reduce((a, p) => a + p.w * p.v, 0) / wsum);
    out.band = classifyBand(out.risk_score);
  }
  out.brief = buildBrief(out);
  return out;
}

// ── LLM-written brief (best-effort) ──────────────────────────────────────────
// The deterministic brief above always ships. When the bot gateway is wired,
// we ALSO ask the agent's LLM for a richer read over the SAME numbers, via the
// account-free public chat path (no identity crosses the boundary). It is
// coalesced + cached ~10 min and keyed on the posture, so a live view doesn't
// hit the model on every poll, and it never blocks: on a cold cache the route
// waits briefly, otherwise it returns instantly with whatever is cached.
const stripTags = (s) => String(s).replace(/<[^>]*>/g, ' ').replace(/&[a-z]+;/gi, ' ').replace(/\s+/g, ' ').trim();
function llmPrompt(m) {
  const parts = [];
  if (m.fear_greed) parts.push(`Fear & Greed ${m.fear_greed.value}/100 (${m.fear_greed.classification})`);
  if (m.market_cap_usd != null) parts.push(`total crypto market cap ${fmtCap(m.market_cap_usd)}${m.market_cap_change_24h != null ? ` (${m.market_cap_change_24h >= 0 ? '+' : ''}${m.market_cap_change_24h.toFixed(1)}% 24h)` : ''}`);
  if (m.btc_dominance != null) parts.push(`BTC dominance ${m.btc_dominance.toFixed(1)}%`);
  if (m.eth_dominance != null) parts.push(`ETH dominance ${m.eth_dominance.toFixed(1)}%`);
  if (m.structure) parts.push(`market structure ${m.structure}`);
  if (m.regime && m.regime.label) parts.push(`the engine's BTC regime reads ${m.regime.label}`);
  if (m.event && m.event.state && m.event.state !== 'NORMAL' && (m.event.next || m.event.active)) {
    parts.push(`macro-event state ${m.event.state} (${(m.event.active || m.event.next).label})`);
  }
  if (m.risk_score != null) parts.push(`blended risk-on score ${m.risk_score}/100 (${m.band ? m.band.label : ''})`);
  return 'You are RUNECLAW, an AI crypto macro strategist. Using ONLY this data (do not invent numbers or news): '
    + parts.join('; ') + '. '
    + 'Write a sharp 3-sentence macro read for a crypto trader, then one short actionable stance line. '
    + 'Plain text, no preamble, no markdown headers.';
}
let _ai = { text: null, ts: 0, key: '', promise: null };
async function llmBrief(m) {
  if (!gateway.isConfigured() || !m.band) return null;
  const key = `${m.band.key}|${Math.round((m.fear_greed ? m.fear_greed.value : 50) / 5)}|${m.structure || ''}`;
  if (_ai.text && _ai.key === key && Date.now() - _ai.ts < 10 * 60000) return _ai.text;
  if (_ai.promise) return _ai.promise;                     // coalesce concurrent cold-cache callers
  _ai.promise = gateway.postGateway('/chat/public', { text: llmPrompt(m) }, 9000)
    .then((r) => {
      const raw = r && r.status === 200 && r.data ? (r.data.reply_html || r.data.reply || '') : '';
      const t = stripTags(raw).slice(0, 700);
      _ai = { text: t || null, ts: Date.now(), key, promise: null };
      return _ai.text;
    })
    .catch(() => { _ai.promise = null; return null; });
  return _ai.promise;
}

// ── GET /api/macro ───────────────────────────────────────────────────────────
router.get('/', async (req, res) => {
  let global = null, fng = null, regime = null;
  try {
    const g = await cached('cg_global', 60000, () => fetchJSON('https://api.coingecko.com/api/v3/global'))();
    const d = g && g.data;
    if (d) global = {
      mcap_usd: d.total_market_cap && d.total_market_cap.usd,
      vol_usd: d.total_volume && d.total_volume.usd,
      btc_dom: d.market_cap_percentage && d.market_cap_percentage.btc,
      eth_dom: d.market_cap_percentage && d.market_cap_percentage.eth,
      mcap_chg_24h: d.market_cap_change_percentage_24h_usd,
    };
  } catch (e) { /* degrade: omit global */ }
  try {
    const f = await cached('fng', 5 * 60000, () => fetchJSON('https://api.alternative.me/fng/?limit=2'))();
    const arr = f && Array.isArray(f.data) ? f.data : [];
    if (arr[0]) fng = { value: arr[0].value, classification: arr[0].value_classification, previous: arr[1] ? arr[1].value : null };
  } catch (e) { /* degrade: omit sentiment */ }
  let calendar = null;
  try {
    const getScan = require('./sync').getLatestScan;
    const scan = typeof getScan === 'function' ? await getScan() : null;
    if (scan && scan.regime) regime = scan.regime;
    if (scan && scan.macro) calendar = scan.macro;
  } catch (e) { /* regime/calendar optional */ }

  const macro = assembleMacro({ global, fng, regime, calendar });
  if (!macro.sources.length) {
    return res.status(502).json({ error: 'Macro data unavailable right now.' });
  }
  try { const ai = await llmBrief(macro); if (ai) macro.ai_brief = ai; } catch (e) { /* fall back to the deterministic brief */ }
  res.set('Cache-Control', 'public, max-age=60');
  res.json({ ok: true, macro, generated_at: new Date().toISOString() });
});

module.exports = router;
module.exports.assembleMacro = assembleMacro;
module.exports.classifyBand = classifyBand;
