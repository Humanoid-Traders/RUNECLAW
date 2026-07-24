'use strict';
/**
 * Paper Trading Arena — the pure engine behind /api/arena.
 *
 * Every registered user gets a paper account with the SAME virtual starting
 * stake, no exchange API keys and no bot gateway required: fills and marks use
 * the public Bitget ticker feed the site already reads. That makes the Arena
 * the zero-friction on-ramp — register, trade live markets risk-free, climb a
 * leaderboard — and the substrate for paper-trading competitions.
 *
 * Mechanics mirror the Stress Lab conventions so the two tools agree:
 * isolated margin, pnl clamped at -margin, liquidation when the return on
 * margin hits -(1-MMR). All functions here are PURE (prices are arguments), so
 * every rule is exactly testable; the route layer feeds live ticker prices.
 *
 * §4: virtual balance only — no real funds ever move, and the PUBLIC
 * leaderboard built on this exposes percent return + opt-in handles only.
 */

const START_BALANCE = 10_000;   // vUSDT — identical for every account
const MMR = 0.005;              // maintenance-margin ratio (matches stress model)
const MIN_MARGIN = 10;          // vUSDT per position
const MAX_LEVERAGE = 20;
const MAX_OPEN = 5;             // open positions per account
const SYMBOL_RE = /^[A-Z0-9]{2,20}$/;

const dirSign = (direction) => (direction === 'SHORT' ? -1 : 1);

/** Unrealized PnL of a position at `mark`, clamped at -margin (isolated). */
function posPnl(pos, mark) {
  const entry = Number(pos.entry), margin = Number(pos.margin), lev = Number(pos.leverage);
  if (!(entry > 0) || !(mark > 0) || !(margin > 0) || !(lev > 0)) return 0;
  const raw = margin * dirSign(pos.direction) * (mark / entry - 1) * lev;
  return Math.max(-margin, raw);
}

/** The mark price at which the position liquidates. */
function liqPrice(pos) {
  const entry = Number(pos.entry), lev = Number(pos.leverage);
  if (!(entry > 0) || !(lev > 0)) return null;
  const move = (1 - MMR) / lev;                    // adverse move that eats the margin
  return dirSign(pos.direction) > 0 ? entry * (1 - move) : entry * (1 + move);
}

/** True when `mark` has crossed the liquidation price. */
function isLiquidated(pos, mark) {
  const lp = liqPrice(pos);
  if (lp == null || !(mark > 0)) return false;
  return dirSign(pos.direction) > 0 ? mark <= lp : mark >= lp;
}

/** Account equity: free balance + margin & unrealized PnL of open positions. */
function equity(balance, positions, marks) {
  let eq = Number(balance) || 0;
  for (const p of positions || []) {
    const mark = marks && marks[p.symbol] && Number(marks[p.symbol].price);
    eq += Number(p.margin) || 0;
    if (mark > 0) eq += posPnl(p, mark);
  }
  return eq;
}

/** Percent return vs the uniform starting stake — the ranking metric. */
function returnPct(eq) {
  return ((Number(eq) || 0) - START_BALANCE) / START_BALANCE * 100;
}

/**
 * Validate an open request against the account state.
 * @returns { ok:true, data:{symbol,direction,margin,leverage} } | { ok:false, error }
 */
function validateOpen(input, balance, openCount) {
  const b = input || {};
  const symbol = String(b.symbol || '').trim().toUpperCase();
  const direction = String(b.direction || '').trim().toUpperCase();
  const margin = Number(b.margin);
  const leverage = Math.round(Number(b.leverage));
  if (!SYMBOL_RE.test(symbol)) return { ok: false, error: 'Invalid symbol' };
  if (direction !== 'LONG' && direction !== 'SHORT') {
    return { ok: false, error: 'direction must be LONG or SHORT' };
  }
  if (!Number.isFinite(margin) || margin < MIN_MARGIN) {
    return { ok: false, error: `margin must be at least ${MIN_MARGIN} vUSDT` };
  }
  if (margin > (Number(balance) || 0)) return { ok: false, error: 'Insufficient balance' };
  if (!Number.isFinite(leverage) || leverage < 1 || leverage > MAX_LEVERAGE) {
    return { ok: false, error: `leverage must be 1–${MAX_LEVERAGE}` };
  }
  if ((openCount || 0) >= MAX_OPEN) {
    return { ok: false, error: `Max ${MAX_OPEN} open positions — close one first` };
  }
  return { ok: true, data: { symbol, direction, margin, leverage } };
}

module.exports = {
  START_BALANCE, MMR, MIN_MARGIN, MAX_LEVERAGE, MAX_OPEN,
  posPnl, liqPrice, isLiquidated, equity, returnPct, validateOpen,
};
