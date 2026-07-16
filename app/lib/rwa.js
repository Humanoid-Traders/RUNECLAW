/**
 * RWA & on-chain radar — READ-ONLY market intelligence.
 *
 * Tracks the tokenized real-world-asset narrative through the venue's OWN
 * live perpetual tickers: a curated, categorized universe of RWA platforms,
 * RWA-narrative chains, and RWA-adjacent DeFi, filtered at runtime to what
 * the exchange actually lists (an unlisted symbol is simply omitted — the
 * radar never shows a market you can't verify live). Aggregates are
 * volume-weighted and always computed, never cached opinions.
 *
 * Deliberately NOT wired to trading: no execution, no signals, no bot
 * behavior change. On-chain/DEX execution remains design-only pending
 * operator + legal review.
 */

const { getTickers } = require('./tickers');

// Curated universe (base coins; hand-verified against Bitget USDT-M
// listings 2026-07). Runtime filtering keeps this honest as listings churn.
const RWA_UNIVERSE = [
  {
    key: 'platforms',
    title: 'RWA platforms & issuers',
    blurb: 'Protocols that tokenize treasuries, credit and funds on-chain.',
    bases: ['ONDO', 'POLYX', 'OM', 'RSR', 'CFG', 'MPL', 'CTC', 'TRU', 'PLUME'],
  },
  {
    key: 'chains',
    title: 'RWA-narrative chains',
    blurb: 'L1/L2s positioning as settlement rails for tokenized assets.',
    bases: ['ETH', 'XRP', 'POL', 'AVAX', 'ALGO', 'HBAR', 'XDC', 'XLM', 'INJ', 'CHZ'],
  },
  {
    key: 'defi',
    title: 'RWA-adjacent DeFi',
    blurb: 'Yield tokenization and lending markets absorbing RWA collateral.',
    bases: ['PENDLE', 'AAVE', 'MKR', 'SKY', 'GFI', 'JTO', 'LDO'],
  },
];

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Build the radar snapshot from a ticker map ({ BTCUSDT: {price, change,
 * volume} }). Pure — injectable tickers make it deterministic in tests.
 */
function buildRadar(tickers) {
  const btc = tickers.BTCUSDT || null;
  const categories = [];
  const all = [];

  for (const cat of RWA_UNIVERSE) {
    const tokens = [];
    for (const base of cat.bases) {
      const tk = tickers[`${base}USDT`];
      if (!tk || !isFinite(tk.price)) continue;      // unlisted → omitted
      tokens.push({
        base,
        price: tk.price,
        change_24h_pct: round2(tk.change),
        volume_24h_usd: Math.round(tk.volume || 0),
      });
    }
    tokens.sort((a, b) => b.change_24h_pct - a.change_24h_pct);
    const vol = tokens.reduce((a, t) => a + t.volume_24h_usd, 0);
    // Volume-weighted 24h change (equal-weight when no volume data).
    let wChange = null;
    if (tokens.length) {
      wChange = vol > 0
        ? tokens.reduce((a, t) => a + t.change_24h_pct * t.volume_24h_usd, 0) / vol
        : tokens.reduce((a, t) => a + t.change_24h_pct, 0) / tokens.length;
    }
    categories.push({
      key: cat.key,
      title: cat.title,
      blurb: cat.blurb,
      tokens,
      listed: tokens.length,
      tracked: cat.bases.length,
      volume_24h_usd: vol,
      change_24h_pct: wChange !== null ? round2(wChange) : null,
    });
    all.push(...tokens);
  }

  const totalVol = all.reduce((a, t) => a + t.volume_24h_usd, 0);
  const sectorChange = all.length
    ? (totalVol > 0
      ? all.reduce((a, t) => a + t.change_24h_pct * t.volume_24h_usd, 0) / totalVol
      : all.reduce((a, t) => a + t.change_24h_pct, 0) / all.length)
    : null;
  const sorted = [...all].sort((a, b) => b.change_24h_pct - a.change_24h_pct);

  return {
    generated_at: new Date().toISOString(),
    source: 'Bitget USDT-M perpetual tickers (live, public)',
    read_only: true,
    sector: {
      listed: all.length,
      volume_24h_usd: totalVol,
      change_24h_pct: sectorChange !== null ? round2(sectorChange) : null,
      vs_btc_pct: sectorChange !== null && btc ? round2(sectorChange - btc.change) : null,
      top_gainer: sorted[0] || null,
      top_loser: sorted.length ? sorted[sorted.length - 1] : null,
    },
    btc_change_24h_pct: btc ? round2(btc.change) : null,
    categories,
  };
}

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

async function getRadar() {
  return buildRadar(await fetchTickers());
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(rwa|real[- ]world assets?|tokeni[sz]ed (assets?|treasuries))\b/i;

function fmtVol(v) {
  const n = Number(v) || 0;
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(n).toLocaleString('en-US');
}

async function maybeHandleRwaChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const r = await getRadar();
    if (!r.sector.listed) {
      return {
        reply_html: 'The RWA radar found none of its tracked tokens listed right now — check the Markets view later.',
        intent: 'rwa',
      };
    }
    const catLines = r.categories.filter(c => c.listed).map((c) => {
      const top = c.tokens.slice(0, 3).map(t =>
        `${t.base} ${t.change_24h_pct >= 0 ? '+' : ''}${t.change_24h_pct}%`).join(' · ');
      return `• <b>${c.title}</b> (${c.listed} listed, ${c.change_24h_pct >= 0 ? '+' : ''}${c.change_24h_pct}% wtd): ${top}`;
    });
    const s = r.sector;
    return {
      reply_html:
        `🏦 <b>RWA radar</b> — live venue tickers, read-only<br><br>`
        + `Sector: <b>${s.change_24h_pct >= 0 ? '+' : ''}${s.change_24h_pct}%</b> (24h, volume-weighted)`
        + (s.vs_btc_pct !== null ? ` — ${s.vs_btc_pct >= 0 ? '+' : ''}${s.vs_btc_pct}% vs BTC` : '')
        + ` · ${s.listed} tokens · ${fmtVol(s.volume_24h_usd)} volume<br>`
        + (s.top_gainer ? `Top: ${s.top_gainer.base} +${s.top_gainer.change_24h_pct}% · ` : '')
        + (s.top_loser ? `Laggard: ${s.top_loser.base} ${s.top_loser.change_24h_pct}%<br><br>` : '<br>')
        + catLines.join('<br>')
        + '<br><br><i>Market intelligence only — the radar never trades. Full table on the Markets view.</i>',
      intent: 'rwa',
    };
  } catch (e) {
    return { reply_html: 'RWA radar is refreshing — try again in a moment.', intent: 'rwa' };
  }
}

module.exports = { RWA_UNIVERSE, buildRadar, getRadar, setTickerFetcher, maybeHandleRwaChat };
