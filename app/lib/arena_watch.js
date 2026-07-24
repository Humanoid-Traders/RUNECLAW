'use strict';
/**
 * Arena liquidation watch — proactive protection for the practice floor.
 *
 * Once a minute (piggybacking the same public-ticker cache the alert engine
 * uses), every open paper position is checked for liquidation proximity; the
 * owner gets ONE web push when a position drifts within WARN_AT of its liq
 * price ("⚠️ BTCUSDT LONG 10× is 2.1% from liquidation"), re-armed only after
 * the position recovers past REARM — so it never spams a hovering market.
 *
 * §4: paper positions only, the push goes to the position's owner only, and
 * the payload carries symbol/direction/percent — no amounts. The decision
 * core (evaluate) is pure so the warn/re-arm hysteresis is exactly testable.
 */

const { pool } = require('../db');
const { getTickers } = require('./tickers');
const { liqPrice } = require('./arena');
const push = require('./push');

const dirSign = (direction) => (direction === 'SHORT' ? -1 : 1);

const WARN_AT = 0.03;   // warn when within 3% of the liquidation price
const REARM = 0.06;     // re-arm only after recovering past 6%

/** Fractional distance from mark to liquidation (negative = crossed). */
function proximity(pos, mark) {
  const lp = liqPrice(pos);
  if (lp == null || !(mark > 0)) return null;
  return dirSign(pos.direction) > 0 ? (mark - lp) / mark : (lp - mark) / mark;
}

/**
 * Pure decision core. @returns { notify: [{position, prox}], warned: Set }
 * `warned` carries position ids already notified; hysteresis: an id leaves
 * the set only when its position is gone or has recovered past REARM.
 */
function evaluate(positions, marks, warned) {
  const next = new Set();
  const notify = [];
  for (const p of positions) {
    const t = marks[p.symbol];
    const prox = t ? proximity(p, Number(t.price)) : null;
    if (prox == null) { if (warned.has(p.id)) next.add(p.id); continue; }
    if (warned.has(p.id)) {
      if (prox <= REARM) next.add(p.id);        // still hot — stay silenced
      continue;                                  // recovered — re-armed
    }
    if (prox <= WARN_AT) {                       // includes crossed (<0): last call
      notify.push({ position: p, prox });
      next.add(p.id);
    }
  }
  return { notify, warned: next };
}

let warnedSet = new Set();
let timer = null;

async function runOnce() {
  if (!push.isConfigured()) return;
  const [positions] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage FROM arena_positions');
  if (!positions.length) { warnedSet = new Set(); return; }
  let marks;
  try { marks = await getTickers(); } catch (e) { return; }   // no data → no verdicts
  const { notify, warned } = evaluate(positions, marks, warnedSet);
  warnedSet = warned;
  for (const n of notify) {
    const p = n.position;
    const pct = Math.max(0, n.prox * 100).toFixed(1);
    await push.notifySubscribers({
      title: '⚠️ Paper position near liquidation',
      body: `${p.symbol} ${p.direction} ${p.leverage}× is ${pct}% from its liquidation price — check the Arena.`,
      url: '/arena',
    }, [p.user_id]).catch(() => {});
  }
}

function startArenaWatch(intervalMs = 60_000) {
  if (timer) return;
  timer = setInterval(() => { runOnce().catch(() => {}); }, intervalMs);
  if (timer.unref) timer.unref();
}

module.exports = { startArenaWatch, runOnce, evaluate, proximity, WARN_AT, REARM };
