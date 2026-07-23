'use strict';
/**
 * MARKETPLACE Phase 3b — follow-an-agent new-pick push watch.
 *
 * The sweep re-derives each followed agent's live gate-matches and pushes to
 * that agent's OPTED-IN followers when a NEW pick appears. Covers: baseline on
 * first sweep (no replay), new-pick detection, dedup, opt-in + follower
 * targeting, and the §4 payload (no dollar figures). Deps are injected, so no
 * DB/gateway/push is needed.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const { sweepCopy, newPicks, resetCopyWatch, baseSym } = require('../lib/copy_watch');

const CATALOG = [
  { id: 'dip-sniper', name: 'Dip Sniper', icon: '🎯', scorecard: { gates: { confidence_threshold: 0.7, regime_filter: 'TREND_DOWN' } } },
];
const SIG = (k, sym, conf, regime, dir = 'LONG') =>
  ({ signal_key: k, symbol: sym, confidence: conf, regime, direction: dir });

test('newPicks returns only matches not already seen', () => {
  const byId = new Map(CATALOG.map(a => [a.id, a]));
  const sigs = [SIG('s1', 'BTC/USDT', 0.8, 'TREND_DOWN'), SIG('s2', 'ETH/USDT', 0.5, 'TREND_DOWN')];
  const fresh = newPicks(byId, sigs, ['dip-sniper'], new Set());
  assert.strictEqual(fresh.length, 1);
  assert.strictEqual(fresh[0].signal.signal_key, 's1');
  // once seen, it's not fresh again
  const seen = new Set(['dip-sniper|s1']);
  assert.strictEqual(newPicks(byId, sigs, ['dip-sniper'], seen).length, 0);
});

test('baseline sweep records current picks and notifies nobody', async () => {
  resetCopyWatch();
  const sends = [];
  const deps = {
    loadFollowedAgentIds: async () => ['dip-sniper'],
    loadSignals: async () => [SIG('s1', 'BTC/USDT', 0.8, 'TREND_DOWN')],
    loadCatalogue: async () => CATALOG,
    loadFollowers: async () => [1, 2],
    loadOptIns: async () => new Set([1, 2]),
  };
  const n = await sweepCopy(deps, async (p, ids) => { sends.push({ p, ids }); return ids.length; });
  assert.strictEqual(n, 0);
  assert.strictEqual(sends.length, 0);   // baseline never pushes
});

test('a new pick pushes to opted-in followers only, then dedups', async () => {
  resetCopyWatch();
  const sends = [];
  const notify = async (p, ids) => { sends.push({ p, ids }); return ids.length; };
  let signals = [SIG('s1', 'BTC/USDT', 0.8, 'TREND_DOWN')];
  const deps = {
    loadFollowedAgentIds: async () => ['dip-sniper'],
    loadSignals: async () => signals,
    loadCatalogue: async () => CATALOG,
    loadFollowers: async () => [1, 2],        // both follow dip-sniper
    loadOptIns: async () => new Set([1]),     // only user 1 opted into push_copy
  };
  // Baseline.
  await sweepCopy(deps, notify);
  // A genuinely new matching signal appears.
  signals = [SIG('s9', 'ETH/USDT', 0.9, 'TREND_DOWN'), ...signals];
  const n = await sweepCopy(deps, notify);
  assert.strictEqual(n, 1);
  assert.strictEqual(sends.length, 1);
  assert.deepStrictEqual(sends[0].ids, [1]);          // user 2 not opted in → excluded
  assert.match(sends[0].p.body, /ETH LONG/);
  // Re-sweeping with no new signal must not re-notify (dedup on seen-set).
  const n2 = await sweepCopy(deps, notify);
  assert.strictEqual(n2, 0);
  assert.strictEqual(sends.length, 1);
});

test('no push when nobody who follows the agent opted in', async () => {
  resetCopyWatch();
  const sends = [];
  let signals = [SIG('s1', 'BTC/USDT', 0.8, 'TREND_DOWN')];
  const deps = {
    loadFollowedAgentIds: async () => ['dip-sniper'],
    loadSignals: async () => signals,
    loadCatalogue: async () => CATALOG,
    loadFollowers: async () => [2, 3],
    loadOptIns: async () => new Set([1]),   // user 1 opted in but doesn't follow this agent
  };
  await sweepCopy(deps, async (p, ids) => { sends.push({ p, ids }); return ids.length; });
  signals = [SIG('s9', 'BTC/USDT', 0.85, 'TREND_DOWN'), ...signals];
  const n = await sweepCopy(deps, async (p, ids) => { sends.push({ p, ids }); return ids.length; });
  assert.strictEqual(n, 0);
  assert.strictEqual(sends.length, 0);
});

test('§4: the push payload carries no dollar figure', async () => {
  resetCopyWatch();
  let signals = [SIG('s1', 'BTC/USDT', 0.8, 'TREND_DOWN')];
  const deps = {
    loadFollowedAgentIds: async () => ['dip-sniper'],
    loadSignals: async () => signals,
    loadCatalogue: async () => CATALOG,
    loadFollowers: async () => [1],
    loadOptIns: async () => new Set([1]),
  };
  let payload = null;
  await sweepCopy(deps, async () => 0);
  signals = [SIG('s9', 'BTC/USDT', 0.9, 'TREND_DOWN'), ...signals];
  await sweepCopy(deps, async (p, ids) => { payload = p; return ids.length; });
  assert.ok(payload);
  const blob = JSON.stringify(payload);
  assert.ok(!blob.includes('$'), 'no dollar amount in a copy push');
  assert.strictEqual(baseSym('BTC/USDT:USDT'), 'BTC');
});

test('push_copy is an opt-in pref and the watcher is wired + reuses agent_match', () => {
  const profile = fs.readFileSync(path.join(__dirname, '..', 'routes', 'profile.js'), 'utf8');
  assert.match(profile, /input\.push_copy === 'boolean'/);   // whitelisted opt-in
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /require\('\.\/lib\/copy_watch'\)\.startCopyWatch\(\)/);
  const watch = fs.readFileSync(path.join(__dirname, '..', 'lib', 'copy_watch.js'), 'utf8');
  assert.match(watch, /require\('\.\/agent_match'\)/);        // reuses the gate matcher
  assert.ok(!/trade\/confirm|live_executor/.test(watch), 'watcher must not touch execution');
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /id="pushCopy"/);
  assert.match(dash, /push_copy: copy\.checked/);
});
