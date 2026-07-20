'use strict';
/**
 * Token safety scanner (PR KK) — deterministic red-flag heuristics folded
 * into the research dossier.
 *
 * Composed only from sources the platform already trusts: the venue's live
 * public tickers (CEX side) and DEXScreener's public pair data (on-chain
 * side, no key — the same source the meme radar uses). Every finding is a
 * HEURISTIC FLAG with a plain-language explanation, never a verdict: "no
 * flags" means "these checks found nothing", not "this token is safe", and
 * the payload says so verbatim.
 *
 * Pure core (`buildSafetyRead`) takes the inputs as values, so tests are
 * deterministic; the DEX pair search is injectable and strictly best-effort
 * (an unreachable API degrades to CEX-only checks, never an error).
 */

const memeRadar = require('./meme');

function num(v) { return (v == null || !isFinite(Number(v))) ? null : Number(v); }

// CEX-side thresholds (24h, venue perp ticker).
const THIN_VOLUME_USD = 2_000_000;
const EXTREME_MOVE_PCT = 25;
const PARABOLIC_MOVE_PCT = 60;
const CEX_DEX_GAP_PCT = 10;

/**
 * Pure. `ticker`: { price, change, volume } from the venue (may be null).
 * `pair`: a DEXScreener pair object for the same base (may be null).
 * `context`: { curated: string[] } — curated universes that track this base.
 */
function buildSafetyRead({ base, ticker, pair, context } = {}) {
  const flags = [];
  const notes = [];

  const price = ticker ? num(ticker.price) : null;
  const change = ticker ? num(ticker.change) : null;
  const volume = ticker ? num(ticker.volume) : null;

  if (volume != null && volume < THIN_VOLUME_USD) {
    flags.push({
      key: 'thin-cex-volume',
      text: `24h venue volume under $${(THIN_VOLUME_USD / 1e6).toFixed(0)}M — thin books slip harder and are easier to push around.`,
    });
  }
  if (change != null && Math.abs(change) >= PARABOLIC_MOVE_PCT) {
    flags.push({
      key: 'parabolic-24h-move',
      text: `${change >= 0 ? '+' : ''}${Math.round(change)}% in 24h — parabolic moves frequently retrace violently; chasing them is how accounts die.`,
    });
  } else if (change != null && Math.abs(change) >= EXTREME_MOVE_PCT) {
    flags.push({
      key: 'extreme-24h-move',
      text: `${change >= 0 ? '+' : ''}${Math.round(change)}% in 24h — outsized move; check what actually happened before touching it.`,
    });
  }

  // On-chain read (best matching DEX pair), reusing the meme radar's
  // normalizer + risk heuristics so CEX-listed and on-chain-only tokens are
  // judged by the SAME yardstick.
  let onchain = null;
  if (pair) {
    const p = memeRadar.normalizePair(pair);
    if (p) {
      const ageHours = p.created_at != null ? (Date.now() - p.created_at) / 3.6e6 : null;
      const risk = memeRadar.riskRead(p.liquidity_usd, ageHours, p.buys_24h, p.sells_24h);
      onchain = {
        chain: p.chain_label,
        dex: p.dex,
        liquidity_usd: p.liquidity_usd,
        price_usd: p.price_usd,
        tier: risk.tier,
      };
      for (const f of risk.flags) {
        const TEXT = {
          'very-low-liquidity': 'On-chain liquidity under $10k — exits at size are effectively impossible.',
          'low-liquidity': 'On-chain liquidity under $50k — even small exits move the price.',
          'under-24h-old': 'Trading pair is under 24 hours old — the classic rug window.',
          'under-1w-old': 'Trading pair is under a week old — no history to judge it by.',
          'no-sells-yet': 'Buys recorded but ZERO sells — a honeypot pattern; assume you cannot exit until proven otherwise.',
          'buys-only-skew': 'Over 90% of transactions are buys — flow this one-sided is often manufactured.',
        };
        flags.push({ key: f, text: TEXT[f] || f });
      }
      if (price != null && p.price_usd != null && price > 0) {
        const gap = Math.abs(p.price_usd - price) / price * 100;
        if (gap >= CEX_DEX_GAP_PCT) {
          flags.push({
            key: 'wide-cex-dex-gap',
            text: `On-chain price differs from the venue by ${Math.round(gap)}% — stale pair or a market someone is leaning on.`,
          });
        }
      }
    }
  } else {
    notes.push('No on-chain pair data reachable — on-chain checks did not run.');
  }

  const curated = (context && context.curated) || [];
  if (curated.length) {
    notes.push(`Tracked in curated RUNECLAW universe${curated.length > 1 ? 's' : ''}: ${curated.join(', ')}.`);
  }

  // Tier: worst signal wins. 'standard' means the checks that RAN found
  // nothing — stated as such, never as "safe".
  const keys = new Set(flags.map(f => f.key));
  let tier = 'standard';
  if (keys.size) tier = 'elevated';
  if (keys.size >= 2 || keys.has('thin-cex-volume') || keys.has('low-liquidity')) tier = 'high';
  if (keys.has('very-low-liquidity') || keys.has('under-24h-old') || keys.has('no-sells-yet')
      || keys.has('parabolic-24h-move')) tier = 'extreme';

  return {
    base: String(base || '').toUpperCase(),
    tier,
    flags,
    notes,
    onchain,
    checks_run: {
      cex: !!ticker,
      onchain: !!onchain,
    },
    disclaimer: 'Heuristic flags from live public data — never a verdict. '
      + 'No flags means these checks found nothing, not that the token is safe.',
  };
}

// ── DEX pair search (injectable, best-effort) ────────────────────────────────

async function searchPairsHttp(base) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 6000);
  try {
    const r = await fetch(
      `https://api.dexscreener.com/latest/dex/search?q=${encodeURIComponent(base)}`,
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
function setPairSearcher(fn) { searchPairs = fn || searchPairsHttp; }

/** Pick the deepest exact-symbol match — depth resists symbol-squatting fakes. */
function bestPairFor(base, pairs) {
  const wanted = String(base || '').toUpperCase();
  const exact = (pairs || []).filter(p =>
    p && p.baseToken && String(p.baseToken.symbol || '').toUpperCase() === wanted);
  if (!exact.length) return null;
  return exact.sort((a, b) =>
    (num(b.liquidity && b.liquidity.usd) || 0) - (num(a.liquidity && a.liquidity.usd) || 0))[0];
}

/** Full scan for a base coin: venue ticker + best on-chain pair + context. */
async function scanToken(base, { ticker, curated } = {}) {
  let pair = null;
  try {
    pair = bestPairFor(base, await searchPairs(base));
  } catch (e) { /* on-chain checks degrade, stated in notes */ }
  return buildSafetyRead({ base, ticker: ticker || null, pair, context: { curated: curated || [] } });
}

module.exports = {
  buildSafetyRead,
  bestPairFor,
  scanToken,
  setPairSearcher,
  THIN_VOLUME_USD,
  EXTREME_MOVE_PCT,
  PARABOLIC_MOVE_PCT,
};
