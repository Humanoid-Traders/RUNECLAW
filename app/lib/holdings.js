/**
 * Funds by venue & wallet — READ-ONLY per-source breakdown.
 *
 * Where the net-worth view collapses everything into ONE real total, this
 * itemises it: one row per connected exchange venue (fetched BOT-side over the
 * gateway, keys never leaving the bot process) and one row per on-chain wallet
 * chain (lib/wallet.js reads). It answers "how much do I have, and *where*".
 *
 * Honesty rules (identical to lib/networth.js):
 *   - Real total sums only real money: every readable venue + the wallet chains.
 *     Paper equity is NOT part of this view.
 *   - A venue or chain that can't be read shows an explicit error row — never a
 *     fabricated zero, which would silently understate the total.
 *   - Strictly read-only: nothing here can place, move, or approve anything.
 */

const gateway = require('./gateway');
const wallet = require('./wallet');

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Build the per-source funds breakdown for a web user.
 *  `ident`  — resolved bot identity ({ id }) for the gateway venue fan-out.
 *  `userId` — web user id, for the SIWE wallet lookup.
 * Both sources fail soft and independently.
 */
async function buildHoldings(ident, userId) {
  const venues = [];          // one row per connected CEX/DEX venue
  const chains = [];          // one row per on-chain wallet chain
  let venuesAvailable = false;
  let walletLinked = false;
  let walletAddress = null;

  // ── Venues: fan-out balance read across every connected venue (bot-side). ──
  if (gateway.isConfigured()) {
    try {
      const r = await gateway.getGateway(
        `/holdings?telegram_id=${encodeURIComponent(ident.id)}`, 35000);
      if (r.status === 200 && r.data && Array.isArray(r.data.venues)) {
        venuesAvailable = true;
        for (const v of r.data.venues) {
          venues.push({
            venue: String(v.venue || 'venue'),
            active: !!v.active,
            ok: !!v.ok,
            equity_usd: (v.ok && isFinite(v.equity_usd)) ? Number(v.equity_usd) : null,
            currency: v.currency || null,
            detail: v.ok ? null : (v.detail || 'unreadable'),
          });
        }
      }
    } catch (e) {
      venuesAvailable = false;
    }
  }

  // ── Wallet: per-chain balances behind the SIWE-linked address. ──
  try {
    const address = await wallet.walletAddressOf(userId);
    if (address) {
      walletLinked = true;
      walletAddress = address;
      const p = await wallet.getWalletPortfolio(address);
      if (p && Array.isArray(p.chains)) {
        for (const c of p.chains) {
          // Skip chains that are both empty and error-free — nothing to show.
          if (!c.assets.length && !c.error) continue;
          chains.push({
            chain: c.chain,
            label: c.label,
            total_usd: c.error ? null : Number(c.total_usd || 0),
            assets: c.assets.length,
            unpriced: c.unpriced || 0,
            detail: c.error || null,
          });
        }
      }
    }
  } catch (e) {
    // walletLinked stays as-is; a read failure just yields no chain rows.
  }

  // ── Real total: only readable real-money sources. ──
  let total = 0;
  let counted = 0;
  for (const v of venues) {
    if (v.ok && isFinite(v.equity_usd)) { total += v.equity_usd; counted++; }
  }
  for (const c of chains) {
    if (c.total_usd != null && isFinite(c.total_usd)) { total += c.total_usd; counted++; }
  }
  const anyUnreadable = venues.some(v => !v.ok) || chains.some(c => c.total_usd == null);

  return {
    read_only: true,
    venues,
    venues_available: venuesAvailable,
    wallet: {
      linked: walletLinked,
      address: walletAddress,
      chains,
    },
    sources_counted: counted,
    total_real_usd: counted ? round2(total) : null,
    partial: anyUnreadable,
    note: 'Every readable venue and on-chain wallet, itemised. A source that '
      + "can't be read is shown as unreadable, never counted as zero. "
      + 'RUNECLAW can read these balances, never move them.',
    generated_at: new Date().toISOString(),
  };
}

module.exports = { buildHoldings };
