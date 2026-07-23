/**
 * RUNECLAW — Portfolio Stress Lab (Digital Twin) simulation model.
 *
 * A pure, deterministic model: given a hypothetical portfolio of leveraged
 * positions and a market-shock scenario, it computes the portfolio drawdown,
 * which leveraged legs get LIQUIDATED, and what breaks you first. Everything is
 * PERCENT of equity — a what-if simulation, never a user's real P&L, so it's §4
 * -clean and needs no account. Not investment advice.
 *
 * Dual export: runs in the browser (window.StressModel) and is require()-able in
 * Node for unit tests — one source of truth for the math.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.StressModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const STABLES = new Set(['USDT', 'USDC', 'DAI', 'TUSD', 'FDUSD', 'USDE', 'BUSD', 'USDD', 'PYUSD', 'GUSD']);
  const MAJORS = new Set(['BTC', 'ETH', 'WBTC', 'WETH', 'XBT']);

  // Maintenance-margin proxy: a leg is liquidated when its loss-on-margin
  // reaches (1 − mmr). Isolated margin, so a leg can lose at most its margin.
  const MMR = 0.005;

  function normAsset(a) {
    return String(a || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/USDT$|USD$|PERP$/, '') || '';
  }
  function classify(asset) {
    const a = normAsset(asset);
    if (STABLES.has(a) || STABLES.has(String(asset || '').toUpperCase())) return 'stable';
    if (MAJORS.has(a)) return 'major';
    return 'alt';
  }

  // Scenarios: per-class price shocks (percent). Alts fall harder than majors;
  // a depeg mostly hits stables with a modest risk-off on the rest.
  const SCENARIOS = [
    { id: 'majors_down', name: 'Majors −30%', emoji: '📉', shocks: { major: -30, alt: -38, stable: 0 } },
    { id: 'alt_crash', name: 'Alt crash −50%', emoji: '🪙', shocks: { major: -18, alt: -50, stable: 0 } },
    { id: 'depeg', name: 'Stablecoin depeg', emoji: '⛓️‍💥', shocks: { major: -6, alt: -10, stable: -7 } },
    { id: 'cascade', name: 'Liquidation cascade', emoji: '🌊', shocks: { major: -25, alt: -45, stable: -1 } },
    { id: 'black_swan', name: 'Black swan', emoji: '🦢', shocks: { major: -55, alt: -70, stable: -4 } },
  ];

  function shockFor(asset, shocks) {
    const s = shocks[classify(asset)];
    return Number.isFinite(s) ? s : 0;
  }

  function num(v, d) { const n = Number(v); return Number.isFinite(n) ? n : d; }
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  /**
   * Simulate one scenario against a portfolio.
   * @param positions [{ asset, weight(% of equity as margin), leverage, dir:'long'|'short' }]
   * @param shocks    { major, alt, stable } percent price moves
   * @returns { drawdownPct, liquidatedCount, legs[], worst, cashPct, severity }
   */
  function simulate(positions, shocks) {
    const legs = [];
    let equityDelta = 0;   // fraction of equity
    let allocated = 0;
    let liquidatedCount = 0;

    for (const p of (positions || [])) {
      const weight = clamp(num(p.weight, 0), 0, 1000);
      if (weight <= 0) continue;
      allocated += weight;
      const lev = clamp(num(p.leverage, 1), 1, 125);
      const dirMult = String(p.dir) === 'short' ? -1 : 1;
      const shock = shockFor(p.asset, shocks) / 100;         // fraction
      const retOnMargin = dirMult * shock * lev;             // return on the margin posted
      const liquidated = retOnMargin <= -(1 - MMR);
      const legFrac = Math.max(-1, retOnMargin);             // isolated: capped at −100% of margin
      const contribution = (weight / 100) * legFrac;         // fraction of equity
      equityDelta += contribution;
      if (liquidated) liquidatedCount++;
      legs.push({
        asset: normAsset(p.asset) || String(p.asset || ''), cls: classify(p.asset),
        weight, leverage: lev, dir: dirMult < 0 ? 'short' : 'long',
        shockPct: shock * 100, retPct: retOnMargin * 100,
        contributionPct: contribution * 100, liquidated,
      });
    }

    // Unallocated equity sits in stables (cash) — moved only by a depeg.
    const cashPct = Math.max(0, 100 - allocated);
    equityDelta += (cashPct / 100) * (num(shocks.stable, 0) / 100);

    const drawdownPct = equityDelta * 100;
    legs.sort((a, b) => a.contributionPct - b.contributionPct);      // worst first
    const worst = legs[0] || null;
    const dd = -drawdownPct;
    const severity = dd >= 50 ? 'critical' : dd >= 25 ? 'severe' : dd >= 10 ? 'notable' : 'resilient';
    return { drawdownPct, liquidatedCount, legs, worst, cashPct, severity };
  }

  /** Run every built-in scenario. */
  function runAll(positions) {
    return SCENARIOS.map((s) => ({ scenario: s, result: simulate(positions, s.shocks) }));
  }

  // ── Shareable portfolio encoding ───────────────────────────────────────────
  // Compact, URL-safe, human-legible: "BTC:40:3:L,ETH:25:2:L,SOL:20:5:S".
  function encodePortfolio(positions) {
    return (positions || [])
      .filter((p) => normAsset(p.asset) && num(p.weight, 0) > 0)
      .slice(0, 24)
      .map((p) => [
        normAsset(p.asset),
        Math.round(clamp(num(p.weight, 0), 0, 1000)),
        Math.round(clamp(num(p.leverage, 1), 1, 125)),
        String(p.dir) === 'short' ? 'S' : 'L',
      ].join(':'))
      .join(',');
  }
  function decodePortfolio(str) {
    if (!str) return [];
    return String(str).split(',').slice(0, 24).map((tok) => {
      const parts = tok.split(':');
      const asset = normAsset(parts[0]);
      if (!asset) return null;
      return {
        asset,
        weight: clamp(num(parts[1], 0), 0, 1000),
        leverage: clamp(num(parts[2], 1), 1, 125),
        dir: parts[3] === 'S' ? 'short' : 'long',
      };
    }).filter(Boolean);
  }

  return { STABLES, MAJORS, classify, SCENARIOS, simulate, runAll, normAsset, encodePortfolio, decodePortfolio };
}));
