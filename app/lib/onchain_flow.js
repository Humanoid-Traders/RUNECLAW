'use strict';
/**
 * On-chain flow radar (PR JJ) — READ-ONLY, keyless.
 *
 * What this measures, honestly: aggregate 24h TAKER flow on public DEX pairs
 * for the majors the engine trades — buys vs sells across the deepest pools
 * (DEXScreener public API, the same feed the meme radar uses). A positive
 * flow bias means on-chain takers are net buying the asset. This is NOT
 * exchange netflow and NOT whale attribution — no paid on-chain provider is
 * involved, and the payload says exactly what it is.
 *
 * The bot's on-chain voter consumes this via the bot-secret sync channel
 * (GET /api/bot/sync/onchain-flow) — gated default-OFF on the engine side.
 * Pure core (`buildFlowRadar`) + injectable per-base pair search for tests.
 */

function num(v) { return (v == null || !isFinite(Number(v))) ? null : Number(v); }
function round3(v) { return Math.round(v * 1000) / 1000; }

// Engine majors with genuinely deep on-chain markets. The DEX-side symbol
// differs for wrapped assets; sparse-DEX majors are deliberately absent —
// a thin sample would manufacture a signal.
const FLOW_BASES = [
  { base: 'BTC', dex_symbol: 'WBTC' },
  { base: 'ETH', dex_symbol: 'WETH' },
  { base: 'SOL', dex_symbol: 'SOL' },
  { base: 'LINK', dex_symbol: 'LINK' },
  { base: 'AVAX', dex_symbol: 'AVAX' },
  { base: 'DOGE', dex_symbol: 'DOGE' },
];

const MIN_PAIR_LIQ_USD = 100_000;   // ignore junk/squatter pools entirely
const TOP_PAIRS = 8;                // deepest N pools per base
const MIN_TXNS = 200;               // below this the bias is damped toward 0

/** Aggregate one base's pairs into a flow row. Pure. */
function flowRow(entry, pairs) {
  const wanted = entry.dex_symbol.toUpperCase();
  const usable = (pairs || [])
    .filter(p => p && p.baseToken
      && String(p.baseToken.symbol || '').toUpperCase() === wanted
      && (num(p.liquidity && p.liquidity.usd) || 0) >= MIN_PAIR_LIQ_USD)
    .sort((a, b) => (num(b.liquidity && b.liquidity.usd) || 0) - (num(a.liquidity && a.liquidity.usd) || 0))
    .slice(0, TOP_PAIRS);
  if (!usable.length) return null;

  let buys = 0, sells = 0, vol = 0, liq = 0;
  for (const p of usable) {
    buys += num(p.txns && p.txns.h24 && p.txns.h24.buys) || 0;
    sells += num(p.txns && p.txns.h24 && p.txns.h24.sells) || 0;
    vol += num(p.volume && p.volume.h24) || 0;
    liq += num(p.liquidity && p.liquidity.usd) || 0;
  }
  const txns = buys + sells;
  if (!txns) return null;
  const buyShare = buys / txns;
  // Raw bias in [-1, 1]; a thin sample is damped proportionally rather than
  // presented at full strength (200 txns of flow ≠ 20,000 txns of flow).
  const damp = Math.min(1, txns / MIN_TXNS);
  return {
    base: entry.base,
    dex_symbol: entry.dex_symbol,
    pairs: usable.length,
    txns_24h: txns,
    buys_24h: buys,
    sells_24h: sells,
    buy_share_pct: round3(buyShare * 100),
    flow_bias: round3((2 * buyShare - 1) * damp),
    volume_24h_usd: Math.round(vol),
    liquidity_usd: Math.round(liq),
    sample: txns >= MIN_TXNS ? 'ok' : 'thin',
  };
}

/** Pure. `pairsByBase`: { BTC: [dexscreener pairs...], ... } */
function buildFlowRadar(pairsByBase) {
  const rows = [];
  const unavailable = [];
  for (const entry of FLOW_BASES) {
    const row = flowRow(entry, (pairsByBase || {})[entry.base]);
    if (row) rows.push(row);
    else unavailable.push(entry.base);
  }
  return {
    generated_at: new Date().toISOString(),
    source: 'DEXScreener public pairs (live, keyless)',
    read_only: true,
    note: '24h taker flow across the deepest DEX pools per asset. This is '
      + 'on-chain taker buy/sell balance — NOT exchange netflow and NOT '
      + 'whale attribution. Thin samples are damped, never dressed up.',
    bases: rows,
    unavailable,
  };
}

// ── Fetch (injectable, best-effort per base) ─────────────────────────────────

async function searchPairsHttp(dexSymbol) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(
      `https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(dexSymbol)}`,
      { signal: ctl.signal, headers: { accept: 'application/json' } });
    if (!r.ok) return null;
    const d = await r.json();
    return Array.isArray(d.pairs) ? d.pairs : null;
  } catch (e) {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

let searchPairs = searchPairsHttp;
function setPairSearcher(fn) { searchPairs = fn || searchPairsHttp; _cache = null; }

const CACHE_MS = 120_000;
let _cache = null;                  // { at, radar }

async function getFlowRadar() {
  if (_cache && Date.now() - _cache.at < CACHE_MS) return _cache.radar;
  const pairsByBase = {};
  // Sequential on purpose: 6 bases, one public API — no burst.
  for (const entry of FLOW_BASES) {
    try {
      pairsByBase[entry.base] = await searchPairs(entry.dex_symbol);
    } catch (e) { /* base degrades to unavailable */ }
  }
  const radar = buildFlowRadar(pairsByBase);
  _cache = { at: Date.now(), radar };
  return radar;
}

module.exports = { FLOW_BASES, flowRow, buildFlowRadar, getFlowRadar, setPairSearcher };
