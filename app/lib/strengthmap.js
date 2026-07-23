'use strict';
/**
 * Strength Map — a factor scoring of the whole Bitget USDT-perp universe, from
 * PUBLIC market data only (price, 24h change, volume, funding, open interest).
 * Each coin gets per-factor scores and a composite long/short strength, which
 * the 3D star-map plots. This is data-viz, NOT investment advice, and it shows
 * only public exchange market data — never a user's account or P&L (§4).
 *
 * Pure functions so the route can cache their output and tests can pin the math.
 */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
function num(v) { const n = Number(v); return Number.isFinite(n) ? n : 0; }
// Smooth squash to (-1, 1); `k` is the input value that maps to ~0.76.
function squash(x, k) { return Math.tanh(num(x) / (k || 1)); }

// Factor weights for the composite directional strength. Illustrative — this is
// a visualization, not a signal. Momentum/trend lead; funding is contrarian
// (crowded longs = a headwind), ΔOI confirms.
const WEIGHTS = { momentum: 0.34, trend: 0.24, doi: 0.14, range: 0.10, funding: 0.18 };

/**
 * scoreTicker(t, prevOiUsd) → one plotted coin, or null if unusable.
 *   t: a Bitget mix ticker { symbol, lastPr, change24h, high24h, low24h,
 *      fundingRate, holdingAmount, usdtVolume }
 *   prevOiUsd: this symbol's OI (usd) from the previous snapshot, for ΔOI.
 */
function scoreTicker(t, prevOiUsd) {
  if (!t || !t.symbol) return null;
  const price = num(t.lastPr);
  if (price <= 0) return null;

  const changePct = num(t.change24h) * 100;               // Bitget change24h is a fraction
  const volumeUsd = num(t.usdtVolume);
  const funding = num(t.fundingRate);                     // fraction, e.g. 0.0001 = 0.01%
  const oiUsd = num(t.holdingAmount) * price;             // holdingAmount is base units
  const hi = num(t.high24h), lo = num(t.low24h);
  // Where price sits in the 24h range: 0 (at low) … 1 (at high).
  const rangePos = hi > lo ? clamp((price - lo) / (hi - lo), 0, 1) : 0.5;
  // ΔOI vs the previous snapshot (percent). 0 on the first poll / no history.
  const doiPct = (prevOiUsd && prevOiUsd > 0) ? ((oiUsd - prevOiUsd) / prevOiUsd) * 100 : 0;

  // Per-factor signed scores in (-1, 1) — the "factor breakdown" the panel shows.
  const factors = {
    momentum: squash(changePct, 8),          // 24h move
    trend: rangePos * 2 - 1,                  // position in the 24h range
    doi: squash(doiPct, 15),                  // open-interest build/unwind
    range: squash((rangePos - 0.5) * 4, 1),   // range extension (RSI-like proxy)
    funding: -squash(funding, 0.0004),        // contrarian: crowded longs → headwind
  };

  // Composite directional strength in (-1, 1), then split into long/short 0..100.
  const dir = clamp(
    WEIGHTS.momentum * factors.momentum + WEIGHTS.trend * factors.trend
    + WEIGHTS.doi * factors.doi + WEIGHTS.range * factors.range
    + WEIGHTS.funding * factors.funding, -1, 1);
  const long_score = Math.round((50 + dir * 50) * 10) / 10;    // 0..100
  const short_score = Math.round((50 - dir * 50) * 10) / 10;

  return {
    symbol: t.symbol,
    base: String(t.symbol).replace(/USDT$/, ''),
    price,
    change_pct: Math.round(changePct * 100) / 100,
    volume_usd: volumeUsd,
    funding,
    oi_usd: oiUsd,
    doi_pct: Math.round(doiPct * 100) / 100,
    range_pos: Math.round(rangePos * 1000) / 1000,
    factors: Object.fromEntries(Object.entries(factors).map(([k, v]) => [k, Math.round(v * 1000) / 1000])),
    long_score,
    short_score,
    dir: Math.round(dir * 1000) / 1000,
  };
}

/**
 * buildStrengthMap(tickers, prevOi, limit) → { coins, at, count }.
 *   tickers: Bitget mix tickers array.
 *   prevOi:  { [symbol]: oi_usd } from the previous poll (for ΔOI), optional.
 *   limit:   keep the top-N by USDT volume (the liquid, plottable universe).
 * Returns coins sorted by volume desc, plus a fresh { [symbol]: oi_usd } map the
 * caller stores as the next poll's `prevOi`.
 */
function buildStrengthMap(tickers, prevOi, limit) {
  const list = Array.isArray(tickers) ? tickers : [];
  const scored = [];
  const oiSnapshot = {};
  for (const t of list) {
    const s = scoreTicker(t, prevOi && prevOi[t && t.symbol]);
    if (!s) continue;
    if (s.volume_usd <= 0) continue;
    oiSnapshot[s.symbol] = s.oi_usd;
    scored.push(s);
  }
  scored.sort((a, b) => b.volume_usd - a.volume_usd);
  const coins = (limit && limit > 0) ? scored.slice(0, limit) : scored;
  return { coins, oiSnapshot, count: coins.length };
}

module.exports = { buildStrengthMap, scoreTicker, WEIGHTS };
