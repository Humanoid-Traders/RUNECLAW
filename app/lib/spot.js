'use strict';
/**
 * Spot market center (SPOT-1, user directive 2026-07-20) — read-only spot
 * market intelligence: every Bitget USDT spot pair, plus the spot↔perp
 * basis where a matching perpetual exists. Same house pattern as the other
 * radars: one fetch, short cache, injectable fetcher, honest failure
 * states. Spot ORDER execution is a separate, explicitly-gated decision —
 * nothing in this module places orders.
 */

const SPOT_URL = process.env.SPOT_TICKERS_URL
  || 'https://api.bitget.com/api/v2/spot/market/tickers';
const TTL_MS = 30_000;
const MAX_PAIRS = 120;

let cache = { at: 0, data: null };
let fetchImpl = null;

function setSpotFetcher(fn) { fetchImpl = fn || null; cache = { at: 0, data: null }; }

async function fetchRaw() {
  if (fetchImpl) return fetchImpl();
  const res = await fetch(SPOT_URL, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`spot tickers HTTP ${res.status}`);
  return res.json();
}

/** All USDT spot pairs, ranked by real 24h quote volume. */
async function getSpotMarket() {
  const now = Date.now();
  if (cache.data && now - cache.at < TTL_MS) return cache.data;
  let raw;
  try { raw = await fetchRaw(); }
  catch (e) {
    return { available: false, reason: 'unreachable', note: String(e.message || e) };
  }
  const pairs = [];
  for (const t of (raw && raw.data) || []) {
    const sym = String(t.symbol || '');
    if (!sym.endsWith('USDT')) continue;
    const price = parseFloat(t.lastPr);
    if (!isFinite(price) || price <= 0) continue;
    pairs.push({
      symbol: sym,
      base: sym.slice(0, -4),
      price,
      change_pct: Math.round(((parseFloat(t.change24h) || 0) * 100) * 100) / 100,
      volume_usdt: Math.round(parseFloat(t.usdtVolume ?? t.quoteVolume) || 0),
      high_24h: parseFloat(t.high24h) || null,
      low_24h: parseFloat(t.low24h) || null,
    });
  }
  pairs.sort((a, b) => b.volume_usdt - a.volume_usdt);
  const out = {
    available: true,
    ranked_by: '24h quote volume (real traded volume)',
    count: pairs.length,
    pairs: pairs.slice(0, MAX_PAIRS),
    note: 'Read-only spot market data. RUNECLAW places no spot orders from '
      + 'this surface — execution is a separate, explicitly confirmed step.',
  };
  cache = { at: now, data: out };
  return out;
}

/** Spot↔perp basis in bps for pairs that exist on both books. Positive =
 * spot trades ABOVE the perp. */
async function getSpotPerpBasis(limit = 12) {
  const spot = await getSpotMarket();
  if (!spot.available) return spot;
  let perps;
  try { perps = await require('./tickers').getTickers(); }
  catch (e) { return { available: false, reason: 'perp_unreachable' }; }
  const rows = [];
  for (const p of spot.pairs) {
    const f = perps[p.symbol];
    if (!f || !isFinite(f.price) || f.price <= 0) continue;
    rows.push({
      symbol: p.symbol,
      spot: p.price,
      perp: f.price,
      basis_bps: Math.round((p.price - f.price) / f.price * 10000 * 10) / 10,
    });
    if (rows.length >= limit) break;
  }
  return {
    available: true,
    rows,
    note: 'Positive basis = spot above perp (perp at a discount). Persistent '
      + 'basis usually reflects funding, not free money.',
  };
}

const CHAT_RE = /\b(spot (market|prices?|pairs?|radar)|spot vs\.? perp|spot basis)\b/i;

function fmt(v) {
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: 6 });
}

async function maybeHandleSpotChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  const [mkt, basis] = [await getSpotMarket(), await getSpotPerpBasis(6)];
  if (!mkt.available) {
    return { reply_html: '🪙 <b>Spot market</b> — venue unreachable right now, try again shortly.' };
  }
  const top = mkt.pairs.slice(0, 8).map(p =>
    `• <b>${p.base}</b> $${fmt(p.price)} (${p.change_pct >= 0 ? '+' : ''}${p.change_pct}%)`);
  const lines = [`🪙 <b>Spot market</b> — ${mkt.count} USDT pairs, top by real volume:`, ...top];
  if (basis.available && basis.rows.length) {
    lines.push('<b>Spot↔perp basis</b> (bps): ' + basis.rows.slice(0, 5)
      .map(r => `${r.symbol.replace('USDT', '')} ${r.basis_bps >= 0 ? '+' : ''}${r.basis_bps}`)
      .join(' · '));
  }
  lines.push(`<i>${mkt.note}</i>`);
  return { reply_html: lines.join('<br>') };
}

module.exports = { getSpotMarket, getSpotPerpBasis, maybeHandleSpotChat, setSpotFetcher, CHAT_RE };
