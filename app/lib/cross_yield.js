/**
 * Cross-chain yield move planner — the net-of-cost breakeven math.
 *
 * Idle capital on one chain earning ~nothing can be relocated to a better rate,
 * but relocating costs gas (exit + arrival) and a bridge fee. A higher APY is
 * only worth chasing once the extra yield out-earns that one-time cost. This is
 * the deterministic engine that answers "is it worth moving, and after how many
 * days does it pay for itself?" — recommendations only, it never moves funds.
 *
 * PURE and side-effect-free: the route feeds it the user's real idle holdings +
 * the real best-available APY (from the idle-yield gateway) + live native-token
 * prices; the cost model here is a transparent ESTIMATE (per-chain typical gas
 * anchor + a bridge-fee bps), always labelled as such — no gas oracle is
 * consulted. Dollars are fine on this per-user private surface (§4).
 */

'use strict';

// Typical cost of an exit/bridge transaction on each chain, in NATIVE token
// units — a conservative anchor, priced live at call time. Estimates, not
// quotes. Keyed to app/lib/wallet.js CHAINS keys (+ solana).
const CHAIN_GAS_ANCHOR = {
  ethereum: { native: 'ETH', units: 0.0040 },
  base: { native: 'ETH', units: 0.00018 },
  arbitrum: { native: 'ETH', units: 0.00030 },
  optimism: { native: 'ETH', units: 0.00022 },
  polygon: { native: 'POL', units: 0.020 },
  bnb: { native: 'BNB', units: 0.00080 },
  solana: { native: 'SOL', units: 0.00002 },
};
// Fallback when the source chain is unknown (typical L2 relocation, in USD).
const UNKNOWN_GAS_USD = 1.50;
// Bridge/withdrawal fee assumption: a few bps of the moved amount, with a floor.
const BRIDGE_FEE_BPS = 8;      // 0.08%
const BRIDGE_FEE_MIN_USD = 0.50;
const DEFAULT_HORIZON_DAYS = 90;

const round2 = (n) => Math.round(n * 100) / 100;
const clampNum = (n) => (Number.isFinite(n) ? n : 0);

/**
 * Estimated one-time cost to relocate `amountUsd` off `fromChain`.
 * `nativePrices` maps native token symbol → USD (e.g. {ETH: 3000, POL: 0.5}).
 * Unknown chain → a flat typical-L2 anchor. Returns dollar components + a note.
 */
function moveCostUsd(amountUsd, fromChain, nativePrices = {}) {
  const amt = Math.max(0, clampNum(amountUsd));
  let gasUsd;
  let gasNote;
  const anchor = CHAIN_GAS_ANCHOR[String(fromChain || '').toLowerCase()];
  if (anchor && Number.isFinite(nativePrices[anchor.native]) && nativePrices[anchor.native] > 0) {
    gasUsd = anchor.units * nativePrices[anchor.native];
    gasNote = `${anchor.units} ${anchor.native} gas`;
  } else {
    gasUsd = UNKNOWN_GAS_USD;
    gasNote = 'typical L2 gas';
  }
  const bridgeUsd = Math.max(amt * (BRIDGE_FEE_BPS / 10000), BRIDGE_FEE_MIN_USD);
  return {
    gas_usd: round2(gasUsd),
    bridge_usd: round2(bridgeUsd),
    total_usd: round2(gasUsd + bridgeUsd),
    estimated: true,
    note: `est. ${gasNote} + ${BRIDGE_FEE_BPS}bps bridge`,
  };
}

/**
 * Breakeven of a single move: extra yield vs the one-time cost.
 * `apyDeltaPct` is the APY gain in percentage points (e.g. 4.2 = +4.2%/yr).
 * Returns breakeven_days (null when there's no positive delta), the net gain
 * over a year and over the horizon, and a plain "worth it?" verdict.
 */
function breakeven(amountUsd, apyDeltaPct, moveCost, horizonDays = DEFAULT_HORIZON_DAYS) {
  const amt = Math.max(0, clampNum(amountUsd));
  const delta = clampNum(apyDeltaPct);
  const cost = Math.max(0, clampNum(moveCost));
  const yearGain = amt * (delta / 100);
  const dailyGain = yearGain / 365;
  const horizonGain = dailyGain * Math.max(1, horizonDays);
  let breakeven_days = null;
  if (dailyGain > 0) breakeven_days = Math.ceil(cost / dailyGain);
  const net_year_usd = round2(yearGain - cost);
  const net_horizon_usd = round2(horizonGain - cost);
  let worth;
  if (delta <= 0 || breakeven_days === null) worth = 'no';
  else if (net_horizon_usd <= 0) worth = 'no';       // won't pay back within the horizon
  else if (breakeven_days > Math.max(1, horizonDays) / 2) worth = 'marginal';
  else worth = 'yes';
  return {
    breakeven_days,
    year_gain_usd: round2(yearGain),
    net_year_usd,
    net_horizon_usd,
    worth,
  };
}

/**
 * Plan a set of candidate moves and rank them by net annual benefit.
 * Each item: { asset, amount_usd, from_chain?, current_apy?, best_apy,
 *   best_source?, custodial?, lockup_days? }. Returns ranked plans; items with
 * no positive APY delta are kept but marked worth:'no' (honest, not hidden).
 */
function planMoves(items, opts = {}) {
  const nativePrices = opts.nativePrices || {};
  const horizonDays = Number.isFinite(opts.horizonDays) ? opts.horizonDays : DEFAULT_HORIZON_DAYS;
  const plans = (Array.isArray(items) ? items : []).map((it) => {
    const amount = clampNum(it && it.amount_usd);
    const bestApy = clampNum(it && it.best_apy);
    const currentApy = clampNum(it && it.current_apy);
    const deltaApy = round2(bestApy - currentApy);
    const cost = moveCostUsd(amount, it && it.from_chain, nativePrices);
    const be = breakeven(amount, deltaApy, cost.total_usd, horizonDays);
    return {
      asset: String((it && it.asset) || '').toUpperCase(),
      amount_usd: round2(amount),
      from_chain: (it && it.from_chain) || null,
      current_apy: round2(currentApy),
      best_apy: round2(bestApy),
      delta_apy: deltaApy,
      best_source: (it && it.best_source) || null,
      custodial: !!(it && it.custodial),
      lockup_days: clampNum(it && it.lockup_days),
      move_cost: cost,
      ...be,
    };
  });
  // Rank: worth-it first, then by net annual benefit descending.
  const rank = { yes: 0, marginal: 1, no: 2 };
  plans.sort((a, b) => (rank[a.worth] - rank[b.worth]) || (b.net_year_usd - a.net_year_usd));
  const worthCount = plans.filter((p) => p.worth === 'yes').length;
  return {
    read_only: true,
    horizon_days: horizonDays,
    plans,
    worth_moving: worthCount,
    caveat: 'Move costs are ESTIMATES (typical gas + a bridge-fee assumption), '
      + 'not live quotes. Rates and costs change — this is guidance, not advice, '
      + 'and nothing here moves your funds.',
  };
}

module.exports = {
  moveCostUsd, breakeven, planMoves,
  CHAIN_GAS_ANCHOR, BRIDGE_FEE_BPS, BRIDGE_FEE_MIN_USD,
  UNKNOWN_GAS_USD, DEFAULT_HORIZON_DAYS,
};
