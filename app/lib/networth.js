/**
 * Unified cross-venue net worth — READ-ONLY aggregation.
 *
 * One view of everything the user holds, combined from three sources that
 * already exist and are already read-only:
 *   cex    — their connected exchange, fetched BOT-side over the gateway
 *            (keys never leave the bot process; same read-only call the
 *            connect-time validators make)
 *   wallet — their SIWE-linked wallet (lib/wallet.js chain reads)
 *   paper  — their simulated paper portfolio
 *
 * Honesty rule: the REAL total sums only real money (cex + wallet). Paper
 * equity is shown as its own clearly-labelled simulated line and is NEVER
 * added into the total.
 */

const gateway = require('./gateway');
const wallet = require('./wallet');

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Build the unified snapshot for a web user. `ident` is the resolved bot
 * identity ({ id }); `userId` the web user id (for the wallet lookup).
 * Every section fails soft to an { available: false } shape.
 */
async function buildNetWorth(ident, userId) {
  const sections = { cex: null, wallet: null, paper: null };

  // Bot gateway: paper + connected-CEX equity in one call.
  if (gateway.isConfigured()) {
    try {
      const r = await gateway.getGateway(
        `/networth?telegram_id=${encodeURIComponent(ident.id)}`, 30000);
      if (r.status === 200 && r.data) {
        sections.paper = r.data.paper || null;
        sections.cex = r.data.cex || null;
      } else {
        sections.cex = { available: false, error: 'gateway' };
      }
    } catch (e) {
      sections.cex = { available: false, error: 'gateway' };
    }
  } else {
    sections.cex = { available: false, error: 'not_configured' };
  }

  // SIWE wallet (web-side chain reads).
  try {
    const address = await wallet.walletAddressOf(userId);
    if (!address) {
      sections.wallet = { linked: false };
    } else {
      const p = await wallet.getWalletPortfolio(address);
      sections.wallet = p
        ? { linked: true, address: p.address, total_usd: p.total_usd,
            assets: p.assets.length, unpriced: p.unpriced }
        : { linked: true, available: false };
    }
  } catch (e) {
    sections.wallet = { linked: true, available: false };
  }

  // Real total: only real money. Paper stays out by design.
  let total = 0;
  let counted = 0;
  if (sections.cex && sections.cex.connected && sections.cex.ok
      && isFinite(sections.cex.equity_usd)) {
    total += Number(sections.cex.equity_usd); counted++;
  }
  if (sections.wallet && sections.wallet.linked
      && isFinite(sections.wallet.total_usd)) {
    total += Number(sections.wallet.total_usd); counted++;
  }

  return {
    read_only: true,
    sections,
    total_real_usd: counted ? round2(total) : null,
    sources_counted: counted,
    note: 'Real total = connected exchange + on-chain wallet. '
      + 'Paper equity is simulated and never included.',
    generated_at: new Date().toISOString(),
  };
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(net ?worth|total (balance|holdings|equity)( across| everywhere)?|balance across (all )?(exchanges|venues)|everything i (own|hold))\b/i;

function fmtUsd(v) {
  return v == null ? '—'
    : '$' + Number(v).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

/**
 * Chat handler. Needs the resolved bot identity, which only the chat route
 * has — so unlike the other intercepts this one takes (ident, userId, text).
 */
async function maybeHandleNetWorthChat(ident, userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const n = await buildNetWorth(ident, userId);
    const lines = [];
    const c = n.sections.cex;
    if (c && c.connected) {
      lines.push(c.ok
        ? `• <b>${(c.venue || 'exchange').toUpperCase()}</b> (connected exchange): <b>${fmtUsd(c.equity_usd)}</b>`
        : `• <b>${(c.venue || 'exchange').toUpperCase()}</b>: unreadable right now (${c.detail || 'venue error'})`);
    } else {
      lines.push('• Exchange: none connected — /connect in Telegram links one (read-only here).');
    }
    const w = n.sections.wallet;
    if (w && w.linked) {
      lines.push(w.total_usd != null
        ? `• <b>Wallet</b> (on-chain, read-only): <b>${fmtUsd(w.total_usd)}</b> across ${w.assets} asset(s)`
        : '• <b>Wallet</b>: linked but unreadable right now.');
    } else {
      lines.push('• Wallet: none linked — Sign-In with Ethereum adds a read-only mirror.');
    }
    const p = n.sections.paper;
    if (p && p.equity_usd != null) {
      lines.push(`• <i>Paper portfolio (simulated, not counted): ${fmtUsd(p.equity_usd)}</i>`);
    }
    return {
      reply_html: `💼 <b>Net worth — everywhere</b> (read-only)<br><br>${lines.join('<br>')}`
        + `<br><br>Real total: <b>${fmtUsd(n.total_real_usd)}</b>`
        + `<br><i>${n.note} RUNECLAW can read these balances, never move them.</i>`,
      intent: 'networth',
    };
  } catch (e) {
    return { reply_html: 'Net-worth read hiccup — try again in a moment.', intent: 'networth' };
  }
}

module.exports = { buildNetWorth, maybeHandleNetWorthChat };
