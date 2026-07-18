/**
 * Web3 deep-research dossiers — "research PENDLE" answered with evidence.
 *
 * A dossier is composed EXCLUSIVELY from sources this platform already
 * trusts and serves, and every section names its source:
 *   • live market read        — venue public tickers
 *   • RWA sector membership   — the curated radar (lib/rwa)
 *   • DEX presence            — Hyperliquid public mids (lib/dex)
 *   • engine signal history   — the recorded signal stream
 *   • recorded trade history  — the agent's actual closed trades
 *
 * Deterministic and honest: no web scraping, no LLM guesswork — a section
 * with no data says so. For the engine's LIVE trade read (levels + LLM
 * thesis), the dossier points at "analyze <coin>", which runs the real
 * analyzer through the bot. On-chain analytics providers that need paid
 * keys (Dune/Arkham-class) are a deliberate non-feature until the operator
 * decides otherwise.
 */

const { pool } = require('../db');
const { getTickers } = require('./tickers');
const rwa = require('./rwa');
const dex = require('./dex');

let fetchTickers = getTickers;
function setTickerFetcher(fn) { fetchTickers = fn || getTickers; }

function round2(v) { return Math.round(v * 100) / 100; }
function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function fmtUsd(v) {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  const dp = n >= 1000 ? 0 : n >= 1 ? 3 : 6;
  return '$' + n.toLocaleString('en-US', { maximumFractionDigits: dp });
}
function fmtVol(v) {
  const n = Number(v) || 0;
  if (n >= 1e9) return '$' + (n / 1e9).toFixed(1) + 'B';
  if (n >= 1e6) return '$' + (n / 1e6).toFixed(1) + 'M';
  return '$' + Math.round(n).toLocaleString('en-US');
}

const NAME_MAP = {
  bitcoin: 'BTC', ethereum: 'ETH', solana: 'SOL', dogecoin: 'DOGE',
  ripple: 'XRP', cardano: 'ADA', avalanche: 'AVAX', chainlink: 'LINK',
};

function baseOf(word) {
  const w = String(word || '').toLowerCase().replace(/^\$/, '');
  if (NAME_MAP[w]) return NAME_MAP[w];
  if (/^[a-z0-9]{2,10}$/.test(w)) return w.toUpperCase().replace(/USDT$/, '');
  return null;
}

/** Build the dossier for a base coin. Returns null when the venue doesn't
 * list it (nothing to research honestly). Every section fails soft. */
async function buildDossier(base) {
  base = String(base || '').toUpperCase();
  let tickers = {};
  try { tickers = await fetchTickers(); } catch (e) { /* market section degraded */ }
  const tk = tickers[`${base}USDT`];
  if (!tk) return null;

  const sections = [];
  const sources = new Set();

  // Market read (always present — it gated entry).
  sections.push({
    title: 'Market read',
    html: `${fmtUsd(tk.price)} · 24h <b class="${tk.change >= 0 ? 'up' : 'down'}">`
      + `${tk.change >= 0 ? '+' : ''}${round2(tk.change)}%</b> · ${fmtVol(tk.volume)} volume`,
    source: 'venue public tickers (live)',
  });
  sources.add('Bitget USDT-M public tickers (live)');

  // RWA sector membership.
  const cat = rwa.RWA_UNIVERSE.find(c => c.bases.includes(base));
  if (cat) {
    try {
      const radar = await rwa.getRadar();
      const rc = radar.categories.find(c => c.key === cat.key);
      sections.push({
        title: 'RWA sector',
        html: `Tracked in <b>${esc(cat.title)}</b> — ${esc(cat.blurb)} `
          + (rc && rc.change_24h_pct != null
            ? `Category 24h (vol-weighted): ${rc.change_24h_pct >= 0 ? '+' : ''}${rc.change_24h_pct}% · `
              + `sector ${radar.sector.change_24h_pct >= 0 ? '+' : ''}${radar.sector.change_24h_pct}%`
              + (radar.sector.vs_btc_pct != null ? ` (${radar.sector.vs_btc_pct >= 0 ? '+' : ''}${radar.sector.vs_btc_pct}% vs BTC)` : '')
            : ''),
        source: 'RWA radar (curated universe over live tickers)',
      });
      sources.add('RUNECLAW RWA radar (live)');
    } catch (e) { /* radar hiccup → skip section */ }
  }

  // DEX presence.
  if (dex.COMPARE.includes(base)) {
    try {
      const cmp = await dex.getDexCompare();
      const row = cmp.rows.find(r => r.base === base);
      if (row) {
        sections.push({
          title: 'DEX presence',
          html: `Trades on Hyperliquid (on-chain perps) at ${fmtUsd(row.dex_mid)}`
            + (row.delta_bps != null
              ? ` — ${row.delta_bps >= 0 ? '+' : ''}${row.delta_bps} bps vs this venue.` : '.'),
          source: 'Hyperliquid public info API (live)',
        });
        sources.add('Hyperliquid public info API (live)');
      }
    } catch (e) { /* skip */ }
  }

  // Engine signal history on this coin.
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, confidence, pattern, status, pnl, created_at
         FROM signals ORDER BY created_at DESC LIMIT 200`, []);
    const mine = rows.filter(s =>
      String(s.symbol || '').toUpperCase().split('/')[0].replace(/USDT.*$/, '') === base);
    if (mine.length) {
      const longs = mine.filter(s => String(s.direction).toUpperCase().includes('LONG')).length;
      const confs = mine.map(s => parseFloat(s.confidence)).filter(isFinite);
      const avgConf = confs.length ? Math.round(confs.reduce((a, b) => a + b, 0) / confs.length * 100) : null;
      const last = mine[0];
      sections.push({
        title: 'Engine signal history',
        html: `${mine.length} recorded signal(s) — ${longs} long / ${mine.length - longs} short`
          + (avgConf !== null ? `, avg confidence ${avgConf}%` : '')
          + `. Latest: ${esc(String(last.direction))}`
          + (last.pattern ? ` (${esc(String(last.pattern))})` : '') + '.',
        source: 'recorded engine signal stream',
      });
      sources.add('RUNECLAW recorded signal stream');
    } else {
      sections.push({
        title: 'Engine signal history',
        html: 'No recorded signals on this coin in the recent stream — the engine has not seen a setup here lately.',
        source: 'recorded engine signal stream',
      });
      sources.add('RUNECLAW recorded signal stream');
    }
  } catch (e) { /* skip */ }

  // Recorded trade history on this coin.
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, fees, opened_at, closed_at
         FROM trades WHERE user_id = ? AND status = 'CLOSED'
          AND closed_at IS NOT NULL ORDER BY closed_at ASC`,
      [parseInt(process.env.BOT_USER_ID) || 1]);
    const mine = rows.filter(t =>
      String(t.symbol || '').toUpperCase().split('/')[0].replace(/USDT.*$/, '') === base);
    if (mine.length) {
      const pnls = mine.map(t => parseFloat(t.pnl) || 0);
      const wins = pnls.filter(p => p > 0).length;
      const net = round2(pnls.reduce((a, b) => a + b, 0));
      sections.push({
        title: 'Agent track record here',
        html: `The agent has closed <b>${mine.length}</b> trade(s) on ${esc(base)}: `
          + `${wins}W/${mine.length - wins}L, net <b class="${net >= 0 ? 'up' : 'down'}">`
          + `${net < 0 ? '-' : '+'}$${Math.abs(net).toFixed(2)}</b>.`,
        source: 'recorded closed trades (public track record data)',
      });
      sources.add('RUNECLAW recorded closed trades');
    }
  } catch (e) { /* skip */ }

  return {
    read_only: true,
    base,
    sections,
    sources: [...sources],
    next_step: `Ask "analyze ${base}" for the engine's live trade read — real levels, `
      + 'run through the full risk-gated analyzer.',
    disclaimer: 'Composed only from live venue data and RUNECLAW\'s own recorded '
      + 'history — no scraped or generated claims. Not financial advice.',
    generated_at: new Date().toISOString(),
  };
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /^(?:please\s+)?(?:research|deep[- ]dive(?:\s+on)?|dossier(?:\s+on)?|due diligence(?:\s+on)?)\s+\$?([a-z0-9]{2,12})\s*$/i;

async function maybeHandleResearchChat(userId, text) {
  const m = String(text || '').trim().match(CHAT_RE);
  if (!m) return null;
  try {
    const base = baseOf(m[1]);
    if (!base) return null;
    const d = await buildDossier(base);
    if (!d) {
      return {
        reply_html: `I can't research <b>${esc(base)}</b> honestly — it isn't listed on `
          + 'the venue, so I have no trusted live data for it.',
        intent: 'research',
      };
    }
    const secs = d.sections.map(s =>
      `<b>${esc(s.title)}</b> <i>· ${esc(s.source)}</i><br>${s.html}`).join('<br><br>');
    return {
      reply_html: `🔬 <b>Research dossier — ${esc(d.base)}</b><br><br>${secs}`
        + `<br><br>➡️ ${esc(d.next_step)}`
        + `<br><br><i>Sources: ${d.sources.map(esc).join(' · ')}. ${esc(d.disclaimer)}</i>`,
      intent: 'research',
    };
  } catch (e) {
    return { reply_html: 'Research desk hiccup — try again in a moment.', intent: 'research' };
  }
}

module.exports = { buildDossier, maybeHandleResearchChat, setTickerFetcher };
