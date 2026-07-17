/**
 * Cross-venue exposure intelligence — READ-ONLY portfolio judgment.
 *
 * Nets the user's holdings per base asset across their open platform
 * positions (perp longs/shorts from their own trades) and their on-chain
 * wallet spot (WETH→ETH, WBTC→BTC mapped), then flags what a risk desk
 * would flag:
 *   stacked_long   — spot AND a perp long on the same asset (doubled bet)
 *   hedged         — spot with a perp short against it (note, not a warning)
 *   concentrated   — one asset is more than half of gross exposure
 *
 * Pure computation + reads; nothing here can resize, hedge, or close
 * anything — it tells the truth and stops.
 */

const { pool } = require('../db');
const wallet = require('./wallet');

function round2(v) { return Math.round(v * 100) / 100; }

// Wrapped assets net against their underlying.
const BASE_MAP = { WETH: 'ETH', WBTC: 'BTC' };
// Stables are cash, not directional exposure.
const STABLES = new Set(['USDT', 'USDC', 'DAI']);

function baseOf(symbol) {
  const raw = String(symbol || '').toUpperCase().split('/')[0].replace(/USDT.*$/, '');
  return BASE_MAP[raw] || raw;
}

/**
 * Pure exposure computation.
 * openTrades: [{symbol, direction, size_usd}] — the user's OPEN positions.
 * walletAssets: [{symbol, usd}] — priced wallet holdings (may be null).
 */
function computeExposure(openTrades, walletAssets) {
  const byBase = new Map();
  const row = (base) => {
    if (!byBase.has(base)) {
      byBase.set(base, { base, perp_long_usd: 0, perp_short_usd: 0, spot_usd: 0 });
    }
    return byBase.get(base);
  };

  for (const t of openTrades || []) {
    const usd = parseFloat(t.size_usd);
    if (!isFinite(usd) || usd <= 0) continue;
    const r = row(baseOf(t.symbol));
    if (String(t.direction).toUpperCase().includes('SHORT')) r.perp_short_usd += usd;
    else r.perp_long_usd += usd;
  }
  let cash_usd = 0;
  for (const a of walletAssets || []) {
    const usd = Number(a.usd);
    if (!isFinite(usd) || usd <= 0) continue;
    const base = baseOf(a.symbol);
    if (STABLES.has(base)) { cash_usd += usd; continue; }
    row(base).spot_usd += usd;
  }

  const assets = [];
  const warnings = [];
  let grossTotal = 0;
  for (const r of byBase.values()) {
    r.net_usd = round2(r.perp_long_usd - r.perp_short_usd + r.spot_usd);
    r.gross_usd = round2(r.perp_long_usd + r.perp_short_usd + r.spot_usd);
    r.perp_long_usd = round2(r.perp_long_usd);
    r.perp_short_usd = round2(r.perp_short_usd);
    r.spot_usd = round2(r.spot_usd);
    r.flags = [];
    if (r.spot_usd > 0 && r.perp_long_usd > 0) {
      r.flags.push('stacked_long');
      warnings.push(`${r.base}: you hold it on-chain AND are long the perp — `
        + `the same bet twice ($${r.gross_usd.toLocaleString('en-US')} gross).`);
    }
    if (r.spot_usd > 0 && r.perp_short_usd > 0) {
      r.flags.push('hedged');
    }
    grossTotal += r.gross_usd;
    assets.push(r);
  }
  for (const r of assets) {
    if (grossTotal > 0 && r.gross_usd / grossTotal > 0.5 && assets.length > 1) {
      r.flags.push('concentrated');
      warnings.push(`${r.base} is ${Math.round(r.gross_usd / grossTotal * 100)}% `
        + 'of your gross exposure — concentration risk.');
    }
  }
  assets.sort((a, b) => b.gross_usd - a.gross_usd);

  return {
    read_only: true,
    assets,
    cash_usd: round2(cash_usd),
    net_total_usd: round2(assets.reduce((a, r) => a + r.net_usd, 0)),
    gross_total_usd: round2(grossTotal),
    warnings,
    note: 'Exposure nets perp positions against on-chain spot (WETH→ETH, '
      + 'WBTC→BTC); stables count as cash. Intelligence only — nothing here '
      + 'can resize or close a position.',
    generated_at: new Date().toISOString(),
  };
}

/** Load the caller's open positions + wallet and compute. Fails soft. */
async function buildExposure(userId) {
  let openTrades = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, size_usd FROM trades
        WHERE user_id = ? AND status = 'OPEN' ORDER BY opened_at DESC`, [userId]);
    openTrades = rows;
  } catch (e) { /* section empty */ }

  let walletAssets = null;
  try {
    const address = await wallet.walletAddressOf(userId);
    if (address) {
      const p = await wallet.getWalletPortfolio(address);
      if (p) walletAssets = p.assets;
    }
  } catch (e) { /* wallet unreadable → perp-only view */ }

  return {
    ...computeExposure(openTrades, walletAssets),
    wallet_included: Array.isArray(walletAssets),
    open_positions: openTrades.length,
  };
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b((my|total|current) exposure|exposure (across|check)|overexposed|doubled? (up|exposure)|how (exposed|leveraged) am i)\b/i;

function fmtUsd(v) {
  return '$' + Number(v || 0).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

async function maybeHandleExposureChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const e = await buildExposure(userId);
    if (!e.assets.length) {
      return {
        reply_html: 'No directional exposure found — no open positions'
          + (e.wallet_included ? ' and no non-stable wallet holdings.' : ', and no wallet linked.'),
        intent: 'exposure',
      };
    }
    const rows = e.assets.slice(0, 8).map((r) => {
      const bits = [];
      if (r.perp_long_usd) bits.push(`long ${fmtUsd(r.perp_long_usd)}`);
      if (r.perp_short_usd) bits.push(`short ${fmtUsd(r.perp_short_usd)}`);
      if (r.spot_usd) bits.push(`spot ${fmtUsd(r.spot_usd)}`);
      const flag = r.flags.includes('stacked_long') ? ' ⚠️'
        : r.flags.includes('hedged') ? ' 🛡 hedged' : '';
      return `• <b>${r.base}</b> net ${fmtUsd(r.net_usd)} (${bits.join(' · ')})${flag}`;
    });
    const warn = e.warnings.length
      ? `<br><br>⚠️ <b>Worth knowing:</b><br>${e.warnings.map(w => `• ${w}`).join('<br>')}`
      : '';
    return {
      reply_html: `🧭 <b>Your exposure — everywhere</b> (read-only)<br><br>${rows.join('<br>')}`
        + `<br><br>Net ${fmtUsd(e.net_total_usd)} · Gross ${fmtUsd(e.gross_total_usd)}`
        + (e.cash_usd ? ` · Cash (stables) ${fmtUsd(e.cash_usd)}` : '')
        + warn
        + `<br><br><i>${e.note}</i>`,
      intent: 'exposure',
    };
  } catch (err) {
    return { reply_html: 'Exposure read hiccup — try again in a moment.', intent: 'exposure' };
  }
}

module.exports = { computeExposure, buildExposure, maybeHandleExposureChat };
