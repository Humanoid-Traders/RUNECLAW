'use strict';
/**
 * Smart venue router (PR QQ) — per-pair best-exchange READS. Manual-first,
 * forever: this module recommends where holding a position is cheapest right
 * now, from data the platform already collects. It never places, routes, or
 * re-routes an order — auto-routing is a separate operator-gated decision
 * that does not exist in this codebase.
 *
 * Inputs (both fail-soft):
 *   - the hourly cross-venue funding scan the bot pushes into reports_cache
 *     ({ base, rates: {venue: APR%}, spread_apr, ... });
 *   - the live Hyperliquid DEX comparison (lib/dex) for on-chain basis
 *     context.
 *
 * Funding mechanics drive the read: positive funding is PAID BY LONGS to
 * shorts. So the cheapest venue to be LONG is the lowest APR (negative =
 * you are paid to be long); the best venue to be SHORT is the highest APR.
 */

const { pool } = require('../db');
const dex = require('./dex');

function num(v) { return (v == null || !isFinite(Number(v))) ? null : Number(v); }
function round2(v) { return Math.round(v * 100) / 100; }

/** Pure. `fundingRows`: reports_cache funding rows; `dexRows`: dex compare rows. */
function buildRouterTable(fundingRows, dexRows) {
  const basisByBase = {};
  for (const r of dexRows || []) {
    if (r && r.base != null && num(r.delta_bps) != null) basisByBase[r.base] = num(r.delta_bps);
  }

  const rows = [];
  for (const f of fundingRows || []) {
    const rates = f && f.rates && typeof f.rates === 'object' ? f.rates : null;
    if (!f || !f.base || !rates) continue;
    const entries = Object.entries(rates)
      .map(([venue, apr]) => [venue, num(apr)])
      .filter(([, apr]) => apr != null);
    if (entries.length < 2) continue;                 // one venue = nothing to route
    entries.sort((a, b) => a[1] - b[1]);
    const [longVenue, longApr] = entries[0];          // lowest APR: cheapest long
    const [shortVenue, shortApr] = entries[entries.length - 1];
    rows.push({
      base: String(f.base),
      venues: Object.fromEntries(entries.map(([v, a]) => [v, round2(a)])),
      long_venue: longVenue,
      long_apr: round2(longApr),
      short_venue: shortVenue,
      short_apr: round2(shortApr),
      spread_apr: round2(shortApr - longApr),
      dex_basis_bps: basisByBase[f.base] ?? null,
    });
  }
  rows.sort((a, b) => b.spread_apr - a.spread_apr);
  return rows;
}

/** Assemble the full payload. Pure given its inputs. */
function buildRouter(fundingReport, dexCompare, reportAgeMs) {
  const rows = buildRouterTable(
    fundingReport && fundingReport.rows, dexCompare && dexCompare.rows);
  return {
    generated_at: new Date().toISOString(),
    read_only: true,
    manual_first: 'Recommendations only — RUNECLAW never auto-routes orders. '
      + 'Costs are annualized funding APRs from the hourly cross-venue scan; '
      + 'verify on the venue before acting.',
    mechanics: 'Positive funding is paid by longs. Lowest APR = cheapest venue '
      + 'to hold a long (negative means you are paid); highest APR = best paid short.',
    report_age_minutes: reportAgeMs != null ? Math.round(reportAgeMs / 60000) : null,
    stale: reportAgeMs != null ? reportAgeMs > 3 * 3600 * 1000 : null,
    rows,
  };
}

// ── Loaders (fail-soft) ──────────────────────────────────────────────────────

async function loadFundingReport() {
  try {
    const [r] = await pool.execute('SELECT reports_json FROM reports_cache WHERE id = 1');
    if (!r.length || !r[0].reports_json) return { report: null, ageMs: null };
    const parsed = JSON.parse(r[0].reports_json);
    const at = parsed.received_at ? new Date(parsed.received_at).getTime() : null;
    return {
      report: parsed.funding || null,
      ageMs: at != null && isFinite(at) ? Math.max(0, Date.now() - at) : null,
    };
  } catch (e) {
    return { report: null, ageMs: null };
  }
}

async function getVenueRouter() {
  const { report, ageMs } = await loadFundingReport();
  let cmp = null;
  try { cmp = await dex.getDexCompare(); } catch (e) { /* basis column degrades */ }
  return buildRouter(report, cmp, ageMs);
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(?:best|cheapest)\s+(?:venue|exchange)(?:\s+(?:for|to)\s+(?:be\s+)?(long|short)?\s*\$?([a-z0-9]{2,10}))?|venue router\b/i;

async function maybeHandleVenueRouterChat(userId, text) {
  const m = String(text || '').match(CHAT_RE);
  if (!m) return null;
  try {
    const r = await getVenueRouter();
    if (!r.rows.length) {
      return {
        reply_html: 'The venue router has no fresh cross-venue funding scan yet — '
          + 'check back after the next hourly report.',
        intent: 'venue_router',
      };
    }
    const wantBase = (m[2] || '').toUpperCase().replace(/USDT$/, '');
    const rows = wantBase ? r.rows.filter(x => x.base === wantBase) : r.rows.slice(0, 5);
    if (!rows.length) {
      return {
        reply_html: `No cross-venue funding data for <b>${wantBase}</b> in the current scan.`,
        intent: 'venue_router',
      };
    }
    const lines = rows.map(x =>
      `• <b>${x.base}</b>: long on <b>${x.long_venue}</b> (${x.long_apr >= 0 ? '+' : ''}${x.long_apr}% APR) · `
      + `short on <b>${x.short_venue}</b> (${x.short_apr >= 0 ? '+' : ''}${x.short_apr}%) · spread ${x.spread_apr}%`);
    return {
      reply_html: `🧭 <b>Venue router</b> — funding-cost read${r.stale ? ' <i>(scan stale)</i>' : ''}<br><br>`
        + lines.join('<br>')
        + `<br><br><i>${r.manual_first}</i>`,
      intent: 'venue_router',
    };
  } catch (e) {
    return { reply_html: 'Venue router is refreshing — try again in a moment.', intent: 'venue_router' };
  }
}

module.exports = { buildRouterTable, buildRouter, getVenueRouter, maybeHandleVenueRouterChat };
