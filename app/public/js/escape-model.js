/**
 * RUNECLAW — Universal Escape Agent model (Guardian).
 *
 * A dependency-aware emergency-unwind PLANNER. Given a portfolio of positions
 * across perps, lending (loans + collateral), LPs, staking, spot and bridged
 * assets, it produces a SAFE, ORDERED exit plan — close the fastest-bleeding
 * leverage first, repay debt to unlock collateral, exit LPs and staking, then
 * convert and bridge home — and it surfaces what's LOCKED and can't move yet.
 *
 * Planning only: it outputs steps and reasons, it never executes anything and
 * touches no funds (§4). Deterministic + pure so tests can assert the ordering.
 *
 * Dual export: browser (window.EscapeModel) + Node (require) for unit tests.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.EscapeModel = api;
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // Unwind order encodes the dependencies: perps bleed fastest → close first;
  // repay debt before its collateral can be withdrawn; exit LP/staking to
  // reclaim the underlying; convert spot; bridge home last.
  const PLAN = {
    perp:       { order: 1, action: 'Close', label: 'perp / derivative', risk: 'high',
      reason: 'Leverage bleeds fastest and can be force-liquidated — flatten derivatives first to stop the bleed.' },
    borrow:     { order: 2, action: 'Repay', label: 'loan / debt', risk: 'high',
      reason: 'Repay debt to lift liquidation risk and to unlock the collateral behind it.' },
    lp:         { order: 3, action: 'Remove liquidity from', label: 'LP position', risk: 'medium',
      reason: 'Exit LPs to stop impermanent-loss drift and reclaim the underlying tokens before moving them.' },
    staked:     { order: 4, action: 'Unstake', label: 'staked asset', risk: 'medium',
      reason: 'Start unstaking early — cooldowns delay everything downstream.' },
    collateral: { order: 5, action: 'Withdraw', label: 'supplied collateral', risk: 'medium',
      reason: 'Withdraw supplied collateral — possible only once the loans it backs are repaid.' },
    spot:       { order: 6, action: 'Convert', label: 'spot holding', risk: 'low',
      reason: 'Convert spot holdings to a stable base — lowest urgency.' },
    bridged:    { order: 7, action: 'Bridge home', label: 'bridged / off-chain asset', risk: 'medium',
      reason: 'Bridge back to your home chain or off-ramp last, once positions are flat.' },
  };
  const TYPES = Object.keys(PLAN);

  function norm(p) {
    const type = TYPES.indexOf(String(p && p.type)) >= 0 ? p.type : 'spot';
    return {
      type,
      asset: String((p && p.asset) || '').trim().slice(0, 20) || '—',
      chain: String((p && p.chain) || '').trim().slice(0, 20),
      backsLoan: !!(p && p.backsLoan),
      nearLiq: !!(p && p.nearLiq),
      locked: !!(p && p.locked),
      lockLabel: String((p && p.lockLabel) || '').trim().slice(0, 40),
    };
  }

  /**
   * @param positions [{ type, asset, chain?, backsLoan?, nearLiq?, locked?, lockLabel? }]
   * @returns { steps[], blockers[], summary, counts }
   */
  function buildPlan(input) {
    const positions = (input || []).map(norm);
    const blockers = [];
    const active = [];
    for (const p of positions) {
      if (p.locked) {
        blockers.push({ asset: p.asset, type: p.type, chain: p.chain,
          reason: p.lockLabel ? ('Locked — ' + p.lockLabel) : 'Locked / vesting — cannot exit until it unlocks.' });
      } else {
        active.push(p);
      }
    }

    const hasBorrow = active.some((p) => p.type === 'borrow');
    const hasSpotOrConvert = active.some((p) => p.type === 'spot' || p.type === 'perp');

    // Stable, dependency-respecting sort: by unwind order, then perps in danger
    // first, then original order (preserved via index).
    const withIdx = active.map((p, i) => ({ p, i }));
    withIdx.sort((a, b) => {
      const oa = PLAN[a.p.type].order, ob = PLAN[b.p.type].order;
      if (oa !== ob) return oa - ob;
      if (a.p.nearLiq !== b.p.nearLiq) return a.p.nearLiq ? -1 : 1;   // urgent perps first
      return a.i - b.i;
    });

    const steps = withIdx.map(function (x, idx) {
      const p = x.p, meta = PLAN[p.type];
      const deps = [];
      if (p.type === 'collateral' && hasBorrow) deps.push('after the loan(s) it backs are repaid');
      if (p.type === 'borrow' && hasSpotOrConvert) deps.push('may need liquid stables first — convert some spot if short');
      const urgent = p.type === 'perp' && p.nearLiq;
      return {
        n: idx + 1,
        action: meta.action,
        type: p.type,
        label: meta.label,
        asset: p.asset,
        chain: p.chain,
        risk: urgent ? 'high' : meta.risk,
        urgent,
        reason: meta.reason,
        depends_on: deps,
      };
    });

    const first = steps[0] || null;
    const summary = !positions.length
      ? 'Add your positions and the Escape Agent will sequence a safe exit.'
      : !steps.length
        ? 'Every position is locked right now — nothing can be unwound until something unlocks (see below).'
        : (first.action + ' your ' + first.label + (first.asset !== '—' ? ' (' + first.asset + ')' : '') + ' first'
          + (steps.some((s) => s.urgent) ? ' — a leveraged leg is near liquidation, move now.' : '.')
          + (blockers.length ? ' ' + blockers.length + ' position(s) are locked and cannot exit yet.' : ''));

    return {
      steps, blockers, summary,
      counts: { positions: positions.length, steps: steps.length, blocked: blockers.length,
        urgent: steps.filter((s) => s.urgent).length },
    };
  }

  return { buildPlan, PLAN, TYPES };
}));
