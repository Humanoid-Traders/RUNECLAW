/**
 * Solver & Counterparty Monitor (Guardian).
 *
 * An autonomous agent that holds real money accumulates counterparty risk that
 * never shows up in a P&L: how much sits with a single centralized custodian,
 * how little is self-custodied ("not your keys"), how concentrated the chains
 * are, and which stablecoin issuer everything settles in. This turns the
 * existing per-venue / per-chain holdings breakdown into a concentration
 * read — Herfindahl (HHI) indices, a custodial-vs-self-custody split, the
 * largest single counterparty, and settlement-issuer concentration — with
 * advisory flags.
 *
 * ADVISORY ONLY — heuristic flags, never a verdict (§4). Concentration is
 * surfaced as ratios; dollar totals mirror what the holdings view already
 * shows the logged-in owner.
 *
 * Scope / honesty: built on balances, not per-trade routing (the trades table
 * has no venue column), so it measures WHERE funds sit, not per-fill best
 * execution or MEV — those need order-level data and are noted follow-ups.
 * Custody type is derived (wallet chains = self-custody; connected venues =
 * custodial CEX/DEX). Stablecoin issuer is inferred from each venue's
 * settlement currency; wallet-held stables are not yet issuer-split upstream.
 *
 * Pure & deterministic — unit-testable object-in / object-out.
 */

'use strict';

function num(v) { const n = typeof v === 'number' ? v : parseFloat(v); return Number.isFinite(n) ? n : 0; }
function round2(n) { return Math.round(n * 100) / 100; }
function round1(n) { return Math.round(n * 10) / 10; }
function pct(part, whole) { return whole > 0 ? round1((part / whole) * 100) : 0; }

// Herfindahl-Hirschman Index over a list of positive weights, scaled to 0–10000
// (10000 = one bucket holds everything; lower = more diversified).
function hhi(weights) {
  const total = weights.reduce((a, w) => a + w, 0);
  if (total <= 0) return 0;
  return Math.round(weights.reduce((a, w) => a + Math.pow(w / total, 2), 0) * 10000);
}

const ISSUER_BY_COIN = { USDT: 'Tether', USDC: 'Circle', USD: 'Circle', DAI: 'MakerDAO', FDUSD: 'First Digital', TUSD: 'TrueUSD' };
function issuerOf(coin) {
  const c = String(coin || '').toUpperCase();
  return ISSUER_BY_COIN[c] || (c ? `Other (${c})` : 'Unknown');
}

/**
 * @param {object} holdings the /api/holdings shape: { venues:[{venue,ok,
 *        equity_usd,currency}], wallet:{ chains:[{label,total_usd}] } }.
 */
function computeCounterparty(holdings) {
  const h = holdings || {};
  const note =
    'Advisory counterparty read — heuristic flags, never a verdict. It measures WHERE your ' +
    'real funds sit (per venue and per chain), not per-fill best execution or MEV, which need ' +
    'order-level data. Custody type is derived (wallet = self-custody; connected venues = ' +
    'custodial); stablecoin issuer is inferred from each venue\'s settlement coin.';

  // Custodial buckets: readable connected venues with positive equity.
  const custodial = (Array.isArray(h.venues) ? h.venues : [])
    .filter(v => v && v.ok && num(v.equity_usd) > 0)
    .map(v => ({ label: v.venue, kind: 'custodial', usd: round2(num(v.equity_usd)), settle: (v.currency || 'USDT') }));

  // Self-custody buckets: on-chain wallet balances per chain.
  const chains = (h.wallet && Array.isArray(h.wallet.chains) ? h.wallet.chains : [])
    .filter(c => c && num(c.total_usd) > 0)
    .map(c => ({ label: c.label || c.chain, kind: 'self_custody', usd: round2(num(c.total_usd)) }));

  const buckets = [...custodial, ...chains].sort((a, b) => b.usd - a.usd);
  const total_usd = round2(buckets.reduce((a, b) => a + b.usd, 0));

  if (!buckets.length || total_usd <= 0) {
    return {
      unrated: true,
      total_usd: 0,
      custodial_usd: 0, self_custody_usd: 0, custodial_pct: 0, self_custody_pct: 0,
      venue_count: 0, chain_count: 0,
      hhi: 0, chain_hhi: 0, concentration: 'none',
      largest: null, buckets: [], issuers: [],
      flags: [{ key: 'no_funds', severity: 'info', label: 'No real balances to assess — connect a venue or link a wallet.' }],
      partial: !!h.partial,
      note,
    };
  }

  const custodial_usd = round2(custodial.reduce((a, b) => a + b.usd, 0));
  const self_custody_usd = round2(chains.reduce((a, b) => a + b.usd, 0));
  const custodial_pct = pct(custodial_usd, total_usd);
  const self_custody_pct = pct(self_custody_usd, total_usd);

  const overallHhi = hhi(buckets.map(b => b.usd));
  const chainHhi = hhi(chains.map(b => b.usd));

  const largestBucket = buckets[0];
  const largest = { label: largestBucket.label, kind: largestBucket.kind, pct: pct(largestBucket.usd, total_usd) };

  // Settlement-issuer concentration across the custodial side.
  const byIssuer = new Map();
  for (const v of custodial) {
    const iss = issuerOf(v.settle);
    byIssuer.set(iss, (byIssuer.get(iss) || 0) + v.usd);
  }
  const issuers = [...byIssuer.entries()]
    .map(([issuer, usd]) => ({ issuer, usd: round2(usd), pct_of_custodial: pct(usd, custodial_usd) }))
    .sort((a, b) => b.usd - a.usd);

  // Concentration headline from the largest single counterparty and how much is
  // custodial overall.
  let concentration;
  if (largest.pct >= 60 || custodial_pct >= 90) concentration = 'high';
  else if (largest.pct >= 35 || custodial_pct >= 70) concentration = 'moderate';
  else concentration = 'low';

  const flags = [];
  if (largest.kind === 'custodial' && largest.pct >= 60) {
    flags.push({ key: 'single_custodian', severity: 'warn', label: `${largest.pct}% of assets sit with a single custodian (${largest.label}) — a single point of failure.` });
  }
  if (custodial_usd > 0 && self_custody_pct < 5) {
    flags.push({ key: 'all_custodial', severity: 'warn', label: 'Almost everything is on centralized venues — not your keys. Consider self-custodying a reserve.' });
  }
  if (issuers.length && issuers[0].pct_of_custodial >= 80 && custodial_usd > 0) {
    flags.push({ key: 'issuer_concentration', severity: 'info', label: `${issuers[0].pct_of_custodial}% of custodial funds settle in one issuer's stablecoin (${issuers[0].issuer}).` });
  }
  if (chains.length >= 2 && chainHhi >= 8000) {
    flags.push({ key: 'chain_concentration', severity: 'info', label: 'Self-custodied funds are concentrated on a single chain.' });
  }
  if (h.partial) {
    flags.push({ key: 'partial', severity: 'info', label: 'Some venues could not be read — figures are partial.' });
  }
  if (!flags.length || (flags.length === 1 && flags[0].key === 'partial')) {
    flags.unshift({ key: 'diversified', severity: 'good', label: 'No counterparty-concentration red flags across your funds.' });
  }

  return {
    unrated: false,
    total_usd,
    custodial_usd, self_custody_usd, custodial_pct, self_custody_pct,
    venue_count: custodial.length, chain_count: chains.length,
    hhi: overallHhi, chain_hhi: chainHhi, concentration,
    largest,
    buckets: buckets.map(b => ({ ...b, pct: pct(b.usd, total_usd) })),
    issuers,
    flags,
    partial: !!h.partial,
    note,
  };
}

module.exports = { computeCounterparty, hhi, issuerOf };
