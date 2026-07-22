'use strict';
/**
 * CROSS-1: cross-chain yield move planner (lib/cross_yield.js).
 *
 * The deterministic net-of-cost breakeven engine: does a better APY out-earn the
 * one-time gas + bridge cost, and after how many days? Every number is real or a
 * transparently-labelled estimate; recommendations only, nothing moves funds.
 * Pure-lib math is exhaustively tested; the route is source-asserted.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const { moveCostUsd, breakeven, planMoves, BRIDGE_FEE_MIN_USD } = require('../lib/cross_yield');

test('moveCostUsd: prices the per-chain gas anchor + bridge fee, flags estimate', () => {
  const c = moveCostUsd(10000, 'ethereum', { ETH: 3000 });
  // 0.004 ETH * 3000 = $12 gas; bridge = max(10000*8bps=$8, $0.50) = $8.
  assert.strictEqual(c.gas_usd, 12);
  assert.strictEqual(c.bridge_usd, 8);
  assert.strictEqual(c.total_usd, 20);
  assert.strictEqual(c.estimated, true);
  assert.match(c.note, /bridge/);
});

test('moveCostUsd: unknown chain / missing price falls back to the flat anchor', () => {
  const c = moveCostUsd(1000, 'zksync', {});
  assert.strictEqual(c.gas_usd, 1.5);              // UNKNOWN_GAS_USD
  assert.match(c.note, /typical L2/);
});

test('moveCostUsd: bridge fee has a floor', () => {
  const c = moveCostUsd(10, 'arbitrum', { ETH: 3000 });   // 10*8bps = $0.008 → floored
  assert.strictEqual(c.bridge_usd, BRIDGE_FEE_MIN_USD);
});

test('breakeven: computes days, net gains, and a verdict', () => {
  // $10k, +4%/yr = $400/yr = ~$1.096/day; cost $20 → ~19 days.
  const b = breakeven(10000, 4, 20, 90);
  assert.strictEqual(b.breakeven_days, 19);
  assert.strictEqual(b.year_gain_usd, 400);
  assert.strictEqual(b.net_year_usd, 380);
  assert.strictEqual(b.worth, 'yes');              // 19d < half of 90d horizon
});

test('breakeven: no positive delta → never worth it, no breakeven day', () => {
  const b = breakeven(10000, 0, 20, 90);
  assert.strictEqual(b.breakeven_days, null);
  assert.strictEqual(b.worth, 'no');
  const neg = breakeven(10000, -1, 20, 90);
  assert.strictEqual(neg.worth, 'no');
});

test('breakeven: pays back but slowly within horizon → marginal', () => {
  // $1k, +3%/yr = $30/yr = ~$0.082/day; cost $20 → ~244 days → beyond a 90d
  // horizon, so it won't pay back in the horizon → "no".
  const slow = breakeven(1000, 3, 20, 90);
  assert.strictEqual(slow.worth, 'no');
  // Larger horizon where it DOES pay back but past the half-horizon → marginal.
  const marg = breakeven(1000, 3, 20, 365);
  assert.ok(marg.breakeven_days > 365 / 2 && marg.breakeven_days <= 365);
  assert.strictEqual(marg.worth, 'marginal');
});

test('planMoves: ranks worth-it first then by net annual benefit, keeps losers visible', () => {
  const out = planMoves([
    { asset: 'usdc', amount_usd: 10000, from_chain: 'ethereum', best_apy: 5 },   // big win
    { asset: 'dai', amount_usd: 200, from_chain: 'ethereum', best_apy: 4 },       // tiny, cost-dominated
    { asset: 'eth', amount_usd: 8000, from_chain: 'arbitrum', best_apy: 0 },      // no yield → no
  ], { nativePrices: { ETH: 3000 }, horizonDays: 90 });
  assert.strictEqual(out.read_only, true);
  assert.strictEqual(out.plans.length, 3);
  // The zero-yield ETH move is worth:'no' and ranked last.
  assert.strictEqual(out.plans[out.plans.length - 1].asset, 'ETH');
  assert.strictEqual(out.plans[out.plans.length - 1].worth, 'no');
  // The big USDC win ranks first and is worth it.
  assert.strictEqual(out.plans[0].asset, 'USDC');
  assert.strictEqual(out.plans[0].worth, 'yes');
  assert.ok(out.worth_moving >= 1);
  assert.match(out.caveat, /estimate|guidance|never/i);
});

test('planMoves: is total garbage-in safe', () => {
  assert.doesNotThrow(() => planMoves(null));
  assert.doesNotThrow(() => planMoves([{}, 42, null]));
  const out = planMoves([{ asset: 'x' }]);
  assert.strictEqual(out.plans[0].worth, 'no');
});

test('route composes idle-yield + planMoves, JWT-authed, read-only', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'routes', 'cross_yield.js'), 'utf8');
  assert.match(src, /authMiddleware/);              // per-user authed
  assert.match(src, /buildIdleYield/);              // real idle holdings + APY
  assert.match(src, /planMoves/);                   // the cost/breakeven engine
  assert.match(src, /walletAddressOf/);             // needs a linked wallet
  assert.ok(!/confirm_trade|postGateway\('\/trade/.test(src), 'read-only — never executes a trade');
});

test('dashboard wires the cross-yield planner card', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /id="p-crossyield"/);
  assert.match(dash, /\/api\/crossyield/);
  assert.match(dash, /worth it|breakeven/i);
});
