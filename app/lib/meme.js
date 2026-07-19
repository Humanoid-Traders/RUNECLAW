/**
 * Meme & AI-agent token radar — READ-ONLY on-chain market intelligence.
 *
 * Sources live DEX pairs from DEXScreener (public API, no key) and presents
 * them with a SAFETY-FORWARD risk read: liquidity depth, pair age, and 24h
 * buy/sell balance — the same signals that will later gate any agent buy.
 * Deliberately non-extractive: the radar never launches tokens and never
 * trades; it exists to help a human (or the agent, read-only) see what's
 * moving on-chain AND how dangerous it is, not to shill.
 *
 * Pure core (`buildRadar`) takes an array of DEXScreener pair objects, so it's
 * deterministic in tests; the network fetch is injectable + best-effort.
 */

const CHAINS = {
  solana: 'Solana', base: 'Base', ethereum: 'Ethereum', bsc: 'BNB Chain',
  arbitrum: 'Arbitrum', polygon: 'Polygon', avalanche: 'Avalanche',
};

function num(v) { return (v == null || !isFinite(Number(v))) ? null : Number(v); }
function round2(v) { return v == null ? null : Math.round(v * 100) / 100; }

// Honest risk read from the on-chain signals DEXScreener gives us. This is the
// SAME liquidity/age/flow read a future agent-buy will gate on — surfaced here
// so nothing is hidden behind a green number.
function riskRead(liqUsd, ageHours, buys, sells) {
  const flags = [];
  if (liqUsd != null && liqUsd < 10_000) flags.push('very-low-liquidity');
  else if (liqUsd != null && liqUsd < 50_000) flags.push('low-liquidity');
  if (ageHours != null && ageHours < 24) flags.push('under-24h-old');
  else if (ageHours != null && ageHours < 168) flags.push('under-1w-old');
  const totalTx = (buys || 0) + (sells || 0);
  if (totalTx >= 20 && sells === 0) flags.push('no-sells-yet');       // can't exit?
  if (totalTx >= 50 && buys / Math.max(totalTx, 1) > 0.9) flags.push('buys-only-skew');
  // Tier: memecoins are high-risk by default; escalate on the flags above.
  let tier = 'high';
  if (flags.includes('very-low-liquidity') || flags.includes('under-24h-old')
      || flags.includes('no-sells-yet')) tier = 'extreme';
  return { tier, flags };
}

function normalizePair(p) {
  if (!p || typeof p !== 'object') return null;
  const price = num(p.priceUsd);
  if (price == null) return null;
  const liq = num(p.liquidity && p.liquidity.usd);
  const vol = num(p.volume && p.volume.h24) || 0;
  const chg = num(p.priceChange && p.priceChange.h24);
  const created = num(p.pairCreatedAt);          // ms epoch
  const buys = num(p.txns && p.txns.h24 && p.txns.h24.buys) || 0;
  const sells = num(p.txns && p.txns.h24 && p.txns.h24.sells) || 0;
  return {
    chain: p.chainId || 'unknown',
    chain_label: CHAINS[p.chainId] || p.chainId || 'unknown',
    dex: p.dexId || null,
    symbol: (p.baseToken && p.baseToken.symbol) || '?',
    name: (p.baseToken && p.baseToken.name) || null,
    address: (p.baseToken && p.baseToken.address) || null,
    quote: (p.quoteToken && p.quoteToken.symbol) || null,
    price_usd: price,
    change_24h_pct: round2(chg),
    volume_24h_usd: Math.round(vol),
    liquidity_usd: liq == null ? null : Math.round(liq),
    fdv_usd: num(p.fdv) == null ? null : Math.round(num(p.fdv)),
    buys_24h: buys, sells_24h: sells,
    created_at: created,
    url: p.url || null,
    _created: created,
  };
}

/**
 * Build the radar from an array of DEXScreener pair objects. Pure.
 * @param {Array} pairs raw DEXScreener pairs
 * @param {number} nowMs current time (ms) — injected so age is deterministic
 */
function buildRadar(pairs, nowMs) {
  const now = num(nowMs) || 0;
  const seen = new Set();
  const rows = [];
  for (const raw of (Array.isArray(pairs) ? pairs : [])) {
    const t = normalizePair(raw);
    if (!t || !t.address) continue;
    const key = `${t.chain}:${t.address}`;
    if (seen.has(key)) continue;                    // dedupe by chain+token
    seen.add(key);
    const ageHours = (t._created && now) ? Math.max(0, (now - t._created) / 3_600_000) : null;
    t.age_hours = ageHours == null ? null : Math.round(ageHours * 10) / 10;
    t.risk = riskRead(t.liquidity_usd, ageHours, t.buys_24h, t.sells_24h);
    delete t._created;
    rows.push(t);
  }

  // Rank by 24h volume (real activity), not price change (pumps).
  rows.sort((a, b) => (b.volume_24h_usd || 0) - (a.volume_24h_usd || 0));

  const byChain = {};
  for (const t of rows) (byChain[t.chain] = byChain[t.chain] || []).push(t);
  const chains = Object.keys(byChain).map((c) => ({
    chain: c,
    chain_label: CHAINS[c] || c,
    count: byChain[c].length,
    volume_24h_usd: byChain[c].reduce((a, t) => a + (t.volume_24h_usd || 0), 0),
  })).sort((a, b) => b.volume_24h_usd - a.volume_24h_usd);

  const extreme = rows.filter(t => t.risk.tier === 'extreme').length;
  const sorted = [...rows].filter(t => t.change_24h_pct != null)
    .sort((a, b) => b.change_24h_pct - a.change_24h_pct);

  return {
    generated_at: new Date().toISOString(),
    source: 'DEXScreener live DEX pairs (public, read-only)',
    read_only: true,
    disclaimer: 'Memecoins are extremely high risk — most go to zero. This is '
      + 'market intelligence with an explicit safety read, NOT advice and NOT a '
      + 'launch tool. The agent never mints tokens.',
    summary: {
      tokens: rows.length,
      volume_24h_usd: rows.reduce((a, t) => a + (t.volume_24h_usd || 0), 0),
      extreme_risk: extreme,
      top_gainer: sorted[0] || null,
      top_by_volume: rows[0] || null,
    },
    chains,
    tokens: rows.slice(0, 40),          // cap payload
  };
}

// ── Network fetch (best-effort, injectable) ─────────────────────────────────

const DS = 'https://api.dexscreener.com';

// Default: trending "boosted" tokens → hydrate to full pairs. Boost is itself a
// promotion signal (surfaced via the risk read), so we never treat it as quality.
async function fetchTrendingPairs() {
  try {
    const boostRes = await fetch(`${DS}/token-boosts/top/v1`,
      { signal: AbortSignal.timeout(10_000) });
    if (!boostRes.ok) return [];
    const boosts = await boostRes.json();
    const addrs = (Array.isArray(boosts) ? boosts : [])
      .map(b => b && b.tokenAddress).filter(Boolean).slice(0, 30);
    if (!addrs.length) return [];
    const res = await fetch(`${DS}/latest/dex/tokens/${addrs.join(',')}`,
      { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) return [];
    const data = await res.json();
    return (data && Array.isArray(data.pairs)) ? data.pairs : [];
  } catch (e) {
    return [];
  }
}

let fetchPairs = fetchTrendingPairs;
function setPairFetcher(fn) { fetchPairs = fn || fetchTrendingPairs; }

async function getRadar() {
  return buildRadar(await fetchPairs(), Date.now());
}

// ── Chat intercept ──────────────────────────────────────────────────────────

const CHAT_RE = /\b(meme ?coins?|meme ?tokens?|dexscreener|degen|pump\.?fun|ai[- ]agent tokens?)\b/i;

function fmtVol(v) {
  const n = Number(v) || 0;
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return '$' + (n / 1e3).toFixed(1) + 'K';
  return '$' + Math.round(n);
}

async function maybeHandleMemeChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const r = await getRadar();
    if (!r.summary.tokens) {
      return { reply_html: 'The meme radar found nothing live right now — the DEXScreener feed may be refreshing.', intent: 'meme' };
    }
    const top = r.tokens.slice(0, 5).map((t) => {
      const chg = t.change_24h_pct == null ? '' : ` ${t.change_24h_pct >= 0 ? '+' : ''}${t.change_24h_pct}%`;
      const risk = t.risk.tier === 'extreme' ? ' ⚠️ extreme' : '';
      return `• <b>${t.symbol}</b> (${t.chain_label})${chg} · ${fmtVol(t.volume_24h_usd)} vol · ${fmtVol(t.liquidity_usd)} liq${risk}`;
    });
    return {
      reply_html:
        `🟣 <b>Meme &amp; AI-token radar</b> — DEXScreener, read-only<br><br>`
        + `${r.summary.tokens} tokens · ${fmtVol(r.summary.volume_24h_usd)} 24h volume · `
        + `<b>${r.summary.extreme_risk}</b> flagged extreme-risk<br><br>`
        + top.join('<br>')
        + '<br><br><i>Memecoins are extremely high risk — most go to zero. This is '
        + 'intelligence with a safety read, not advice. The agent never launches tokens.</i>',
      intent: 'meme',
    };
  } catch (e) {
    return { reply_html: 'Meme radar is refreshing — try again in a moment.', intent: 'meme' };
  }
}

module.exports = {
  CHAINS, riskRead, normalizePair, buildRadar,
  getRadar, setPairFetcher, maybeHandleMemeChat,
};
