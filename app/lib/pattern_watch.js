'use strict';
/**
 * Pattern watch — the engine's read, pushed to the people it concerns.
 *
 * Every two minutes the latest synced deep-scan pattern block is checked
 * against OPEN paper positions: when the engine's detectors report a NEW
 * high-confidence chart pattern (Elliott, Wyckoff, H&S, …) on a symbol a
 * user is holding, that user gets ONE web push ("🧭 BTCUSDT: Elliott 5-Wave
 * Impulse (78%) — you have a LONG open"). Dedupe is per
 * (user, symbol, pattern) with a 12h TTL — a persistent pattern never
 * spams, and a fresh read after it fades can announce again.
 *
 * One source of truth: patterns come ONLY from the engine's own synced
 * deepscan block — nothing is re-detected here. §4: the push goes to the
 * position's owner only and carries symbol + pattern name + confidence
 * percent (the engine's read of public market structure) — no amounts.
 */

const { pool } = require('../db');
const push = require('./push');

const MIN_CONFIDENCE = 0.6;      // engine confidence floor for a push
const TTL_MS = 12 * 3600 * 1000; // per (user, symbol, pattern) silence window
const MAX_PER_SYMBOL = 2;        // top patterns per symbol per sweep

const base = (sym) => String(sym || '').toUpperCase()
  .replace('/USDT', '').replace(':USDT', '').replace(/USDT$/, '');

/**
 * Pure decision core.
 * @param hits deepscan hits [{symbol, chart_patterns: [{name, signal, confidence}]}]
 * @param positions open paper positions [{user_id, symbol, direction}]
 * @param seen Map "user|base|pattern" -> announced-at ts
 * @returns { notify: [{user_id, symbol, direction, name, signal, confidence}], seen: nextMap }
 */
function transitions(hits, positions, seen, now = Date.now()) {
  const bySym = new Map();
  for (const h of hits || []) {
    const b = base(h.symbol);
    if (!b) continue;
    bySym.set(b, (h.chart_patterns || [])
      .filter((p) => p && p.name && (Number(p.confidence) || 0) >= MIN_CONFIDENCE)
      .slice(0, MAX_PER_SYMBOL));
  }
  const next = new Map();
  for (const [k, ts] of seen || []) {
    if (now - ts < TTL_MS) next.set(k, ts);
  }
  const notify = [];
  for (const pos of positions || []) {
    const pats = bySym.get(base(pos.symbol));
    if (!pats || !pats.length) continue;
    for (const pat of pats) {
      const key = `${pos.user_id}|${base(pos.symbol)}|${pat.name}`;
      if (next.has(key)) continue;
      next.set(key, now);
      notify.push({
        user_id: pos.user_id, symbol: pos.symbol, direction: pos.direction,
        name: pat.name, signal: pat.signal || 'neutral',
        confidence: Number(pat.confidence) || 0,
      });
    }
  }
  return { notify, seen: next };
}

let seenMap = new Map();
let timer = null;

async function runOnce() {
  if (!push.isConfigured()) return;
  let scan;
  try {
    // Lazy require: routes/sync owns the synced scan cache; requiring at
    // call time avoids any boot-order cycle.
    scan = await require('../routes/sync').getLatestScan();
  } catch (e) { return; }
  const hits = scan && scan.deepscan && scan.deepscan.hits;
  if (!hits || !hits.length) return;
  const [positions] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage FROM arena_positions');
  if (!positions.length) return;
  const { notify, seen } = transitions(hits, positions, seenMap);
  seenMap = seen;
  for (const n of notify) {
    await push.notifySubscribers({
      title: '🧭 Engine pattern on your symbol',
      body: `${n.symbol}: ${n.name} (${Math.round(n.confidence * 100)}%, ${n.signal}) — `
        + `you have a ${n.direction} open. See the chart in the Arena.`,
      url: '/arena',
    }, [n.user_id]).catch(() => {});
  }
}

function startPatternWatch(intervalMs = 120_000) {
  if (timer) return;
  timer = setInterval(() => { runOnce().catch(() => {}); }, intervalMs);
  if (timer.unref) timer.unref();
}

module.exports = { startPatternWatch, runOnce, transitions, MIN_CONFIDENCE, TTL_MS, MAX_PER_SYMBOL };
