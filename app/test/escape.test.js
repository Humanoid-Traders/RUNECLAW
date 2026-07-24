'use strict';
/**
 * Universal Escape Agent (Guardian) — a dependency-aware emergency-unwind
 * PLANNER. Pure + deterministic: it sequences a safe exit (close leverage →
 * repay debt → unlock collateral → exit LP/staking → convert → bridge) and
 * surfaces locked positions. §4: planning only — it never executes or touches
 * funds, and carries no dollar figures.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const E = require('../public/js/escape-model');

const FULL = [
  { type: 'spot', asset: 'ARB' },
  { type: 'bridged', asset: 'USDC', chain: 'Polygon' },
  { type: 'collateral', asset: 'ETH', chain: 'Aave' },
  { type: 'borrow', asset: 'USDT', chain: 'Aave' },
  { type: 'perp', asset: 'SOL', nearLiq: true },
  { type: 'lp', asset: 'ETH/USDC' },
  { type: 'staked', asset: 'ETH', chain: 'Lido' },
];

test('the plan unwinds in dependency order, urgent leverage first', () => {
  const plan = E.buildPlan(FULL);
  const order = plan.steps.map(s => s.type);
  assert.deepEqual(order, ['perp', 'borrow', 'lp', 'staked', 'collateral', 'spot', 'bridged']);
  assert.equal(plan.steps[0].type, 'perp');
  assert.equal(plan.steps[0].urgent, true);           // near-liquidation perp is flagged
  assert.equal(plan.counts.urgent, 1);
});

test('collateral withdrawal depends on repaying the loan behind it', () => {
  const plan = E.buildPlan(FULL);
  const collat = plan.steps.find(s => s.type === 'collateral');
  assert.ok(collat.depends_on.join(' ').match(/repaid/));
  // and repay comes before withdraw in the sequence
  const repayN = plan.steps.find(s => s.type === 'borrow').n;
  assert.ok(repayN < collat.n);
});

test('locked / vesting positions become blockers, not steps', () => {
  const plan = E.buildPlan([
    { type: 'spot', asset: 'ETH' },
    { type: 'staked', asset: 'ATOM', locked: true, lockLabel: 'unbonding 21 days' },
  ]);
  assert.equal(plan.steps.length, 1);
  assert.equal(plan.blockers.length, 1);
  assert.match(plan.blockers[0].reason, /unbonding 21 days/);
});

test('empty and all-locked portfolios are handled', () => {
  assert.equal(E.buildPlan([]).steps.length, 0);
  const allLocked = E.buildPlan([{ type: 'staked', asset: 'X', locked: true }]);
  assert.equal(allLocked.steps.length, 0);
  assert.equal(allLocked.counts.blocked, 1);
  assert.match(allLocked.summary, /locked/i);
});

test('§4: planning only — no execution, no dollar figures', () => {
  const raw = JSON.stringify(E.buildPlan(FULL)).toLowerCase();
  assert.ok(!raw.includes('$'));
  // actions describe what the human should do; the model never claims to act
  for (const forbidden of ['executed', 'order placed', 'signed the', 'auto-']) {
    assert.ok(!raw.includes(forbidden));
  }
});

test('the /escape page + route + Guardian card + nav are wired', () => {
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /app\.get\('\/escape'/);
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'escape.html'), 'utf8');
  assert.match(html, /js\/escape-model\.js/);
  assert.match(html, /EscapeModel/);
  assert.match(html, /never executes|planning only|does not\s*\n?\s*move funds/i);
  assert.match(html, /not investment advice/i);
  const gd = fs.readFileSync(path.join(__dirname, '..', 'public', 'guardian.html'), 'utf8');
  assert.match(gd, /href="\/escape"/);
  assert.match(gd, /Universal Escape Agent<\/span><span class="st live">/);   // promoted to live
  assert.ok(!/class="card soon"/.test(gd), 'every Guardian module is now live');
  const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(index, /href="\/escape"/);
});
