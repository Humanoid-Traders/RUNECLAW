'use strict';
/**
 * Spot market center (SPOT-1, user directive 2026-07-20) — read-only spot
 * market intelligence across EVERY connected venue that has a spot book:
 * Bitget, Bybit and BingX today (Hyperliquid spot uses a nonstandard
 * symbol scheme and lands as a follow-up). Same house pattern as the other
 * radars: short cache, injectable per-venue fetchers, honest per-venue
 * failure states. Spot ORDER execution is a separate, explicitly-gated
 * decision — nothing in this module places orders.
 */

const TTL_MS = 30_000;
const MAX_PAIRS = 120;

// Per-venue public spot-ticker endpoints + normalizers → {symbol(BASEUSDT),
// price, change_pct, volume_usdt, high_24h, low_24h}.
const VENUES = {
  bitget: {
    url: process.env.SPOT_TICKERS_URL
      || 'https://api.bitget.com/api/v2/spot/market/tickers',
    parse(raw) {
      return ((raw && raw.data) || []).map(t => ({
        symbol: String(t.symbol || ''),
        price: parseFloat(t.lastPr),
        change_pct: (parseFloat(t.change24h) || 0) * 100,
        volume_usdt: parseFloat(t.usdtVolume ?? t.quoteVolume) || 0,
        high_24h: parseFloat(t.high24h) || null,
        low_24h: parseFloat(t.low24h) || null,
      }));
    },
  },
  bybit: {
    url: 'https://api.bybit.com/v5/market/tickers?category=spot',
    parse(raw) {
      return (((raw || {}).result || {}).list || []).map(t => ({
        symbol: String(t.symbol || ''),
        price: parseFloat(t.lastPrice),
        change_pct: (parseFloat(t.price24hPcnt) || 0) * 100,
        volume_usdt: parseFloat(t.turnover24h) || 0,
        high_24h: parseFloat(t.highPrice24h) || null,
        low_24h: parseFloat(t.lowPrice24h) || null,
      }));
    },
  },
  bingx: {
    url: 'https://open-api.bingx.com/openApi/spot/v1/ticker/24hr',
    parse(raw) {
      return ((raw && raw.data) || []).map(t => ({
        symbol: String(t.symbol || '').replace('-', ''),
        price: parseFloat(t.lastPrice),
        change_pct: parseFloat(t.priceChangePercent) || 0,
        volume_usdt: parseFloat(t.quoteVolume) || 0,
        high_24h: parseFloat(t.highPrice) || null,
        low_24h: parseFloat(t.lowPrice) || null,
      }));
    },
  },
};

let cache = { at: 0, data: null };
let fetchers = {};   // injected per-venue fetchers (tests / alternate routing)

/**
 * Inject a fetcher. `setSpotFetcher(fn)` injects Bitget (back-compat);
 * `setSpotFetcher(fn, 'bybit')` injects that venue. IMPORTANT: while ANY
 * fetcher is injected, non-injected venues are skipped — tests stay
 * hermetic, never touching the network. `setSpotFetcher(null)` clears all.
 */
function setSpotFetcher(fn, venue = 'bitget') {
  if (fn == null && venue === 'bitget') fetchers = {};
  else if (fn == null) delete fetchers[venue];
  else fetchers[venue] = fn;
  cache = { at: 0, data: null };
}

async function fetchVenue(id) {
  const v = VENUES[id];
  if (fetchers[id]) return v.parse(await fetchers[id]());
  const res = await fetch(v.url, { signal: AbortSignal.timeout(10_000) });
  if (!res.ok) throw new Error(`${id} spot HTTP ${res.status}`);
  return v.parse(await res.json());
}

/** All USDT spot pairs across venues, ranked by real 24h quote volume. */
async function getSpotMarket() {
  const now = Date.now();
  if (cache.data && now - cache.at < TTL_MS) return cache.data;
  const injected = Object.keys(fetchers).length > 0;
  const ids = Object.keys(VENUES).filter(id => !injected || fetchers[id]);
  const settled = await Promise.allSettled(ids.map(id => fetchVenue(id)));

  const venues = {};
  const bySymbol = new Map();
  ids.forEach((id, i) => {
    if (settled[i].status !== 'fulfilled') {
      venues[id] = { ok: false, error: String(settled[i].reason && settled[i].reason.message || 'error') };
      return;
    }
    let kept = 0;
    for (const t of settled[i].value) {
      if (!t.symbol.endsWith('USDT') || !isFinite(t.price) || t.price <= 0) continue;
      kept++;
      const row = bySymbol.get(t.symbol) || {
        symbol: t.symbol, base: t.symbol.slice(0, -4), venues: {},
      };
      row.venues[id] = {
        price: t.price,
        change_pct: Math.round(t.change_pct * 100) / 100,
        volume_usdt: Math.round(t.volume_usdt),
        high_24h: t.high_24h, low_24h: t.low_24h,
      };
      bySymbol.set(t.symbol, row);
    }
    venues[id] = { ok: true, pairs: kept };
  });

  if (![...Object.values(venues)].some(v => v.ok)) {
    return { available: false, reason: 'unreachable', venues };
  }

  // Flatten: primary quote = highest-volume venue for each symbol; keep the
  // full per-venue map + the cross-venue spread where 2+ venues list it.
  const pairs = [...bySymbol.values()].map(row => {
    const entries = Object.entries(row.venues);
    entries.sort((a, b) => b[1].volume_usdt - a[1].volume_usdt);
    const [primaryVenue, p] = entries[0];
    const prices = entries.map(e => e[1].price);
    const spreadBps = entries.length > 1
      ? Math.round((Math.max(...prices) - Math.min(...prices))
          / Math.min(...prices) * 10000 * 10) / 10
      : null;
    return {
      symbol: row.symbol, base: row.base,
      price: p.price, change_pct: p.change_pct,
      volume_usdt: entries.reduce((s, e) => s + e[1].volume_usdt, 0),
      high_24h: p.high_24h, low_24h: p.low_24h,
      venue: primaryVenue, listed_on: entries.map(e => e[0]),
      venue_spread_bps: spreadBps,
      per_venue: row.venues,
    };
  });
  pairs.sort((a, b) => b.volume_usdt - a.volume_usdt);

  const out = {
    available: true,
    ranked_by: '24h quote volume (real traded volume), summed across venues',
    venues,
    count: pairs.length,
    pairs: pairs.slice(0, MAX_PAIRS),
    note: 'Read-only spot market data across all connected venues with a '
      + 'spot book. RUNECLAW places no spot orders from this surface — '
      + 'execution is a separate, explicitly confirmed step.',
  };
  cache = { at: now, data: out };
  return out;
}

/** Spot↔perp basis in bps for pairs on both books (perp book = Bitget
 * USDT-M). Positive = spot trades ABOVE the perp. */
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
      spot_venue: p.venue,
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
    return { reply_html: '🪙 <b>Spot market</b> — no spot venue reachable right now, try again shortly.' };
  }
  const up = Object.entries(mkt.venues).filter(([, v]) => v.ok).map(([id]) => id);
  const top = mkt.pairs.slice(0, 8).map(p =>
    `• <b>${p.base}</b> $${fmt(p.price)} (${p.change_pct >= 0 ? '+' : ''}${p.change_pct}%)`
    + (p.venue_spread_bps ? ` <i>${p.listed_on.length} venues, spread ${p.venue_spread_bps} bps</i>` : ''));
  const lines = [
    `🪙 <b>Spot market</b> — ${mkt.count} USDT pairs across ${up.join(' + ')}, top by real volume:`,
    ...top];
  if (basis.available && basis.rows.length) {
    lines.push('<b>Spot↔perp basis</b> (bps): ' + basis.rows.slice(0, 5)
      .map(r => `${r.symbol.replace('USDT', '')} ${r.basis_bps >= 0 ? '+' : ''}${r.basis_bps}`)
      .join(' · '));
  }
  lines.push(`<i>${mkt.note}</i>`);
  return { reply_html: lines.join('<br>') };
}

module.exports = { getSpotMarket, getSpotPerpBasis, maybeHandleSpotChat, setSpotFetcher, CHAT_RE, VENUES };
