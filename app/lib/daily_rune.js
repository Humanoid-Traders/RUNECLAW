'use strict';
/**
 * Today on RUNECLAW — the daily digest, assembled ONLY from data the
 * platform already holds: the engine's synced deep-scan (top pattern read
 * of the moment), today's signal tape (created / resolved / wins), the
 * Arena pulse (traders + closes in 24h) and the current season. Anything
 * missing is OMITTED — the digest never invents a headline.
 *
 * §4: public surface — pattern names + confidence percent (the engine's
 * read of public market structure), counts, and win rates only. No dollar
 * amounts of any kind.
 */

const { pool } = require('../db');

/** Pure assembly from pre-fetched pieces; every part optional. */
function buildToday(parts) {
  const out = { generated_at: new Date().toISOString(), virtual: true };
  const hits = parts && parts.scan && parts.scan.deepscan && parts.scan.deepscan.hits;
  if (hits && hits.length) {
    let best = null;
    for (const h of hits) {
      for (const p of h.chart_patterns || []) {
        const c = Number(p.confidence) || 0;
        if (p.name && (!best || c > best.confidence)) {
          best = { symbol: h.symbol, name: p.name, signal: p.signal || 'neutral', confidence: c };
        }
      }
    }
    if (best && best.confidence >= 0.55) out.top_pattern = best;
  }
  const sig = parts && parts.signals;
  if (sig && (sig.created_today || sig.resolved_today)) {
    out.signals = {
      created_today: sig.created_today || 0,
      resolved_today: sig.resolved_today || 0,
      wins_today: sig.wins_today || 0,
    };
  }
  const ar = parts && parts.arena;
  if (ar && (ar.traders || ar.closes_24h)) {
    out.arena = { traders: ar.traders || 0, closes_24h: ar.closes_24h || 0 };
  }
  if (parts && parts.season) out.season = parts.season;
  return out;
}

/** Fetch + assemble; every source fails open to "omitted". */
async function fetchToday() {
  const parts = {};
  try {
    parts.scan = await require('../routes/sync').getLatestScan();
  } catch (e) { /* no scan yet */ }
  try {
    const dayStart = new Date();
    dayStart.setUTCHours(0, 0, 0, 0);
    const [c] = await pool.execute(
      'SELECT COUNT(*) AS n FROM signals WHERE created_at >= ?', [dayStart]);
    const [rows] = await pool.execute(
      'SELECT pnl FROM signals WHERE resolved_at >= ?', [dayStart]);
    parts.signals = {
      created_today: Number(c[0] && c[0].n) || 0,
      resolved_today: rows.length,
      wins_today: rows.filter((r) => Number(r.pnl) > 0).length,
    };
  } catch (e) { /* quiet tape */ }
  try {
    const [accounts] = await pool.execute('SELECT user_id, balance FROM arena_accounts');
    const [cnt] = await pool.execute(
      'SELECT COUNT(*) AS n FROM arena_trades WHERE closed_at >= ?',
      [new Date(Date.now() - 24 * 3600 * 1000)]);
    parts.arena = { traders: accounts.length, closes_24h: Number(cnt[0] && cnt[0].n) || 0 };
  } catch (e) { /* arena quiet */ }
  try {
    const seasons = require('./arena_seasons');
    const [srows] = await pool.execute(
      'SELECT id, name, starts_at, ends_at FROM arena_seasons');
    if (srows[0]) {
      const s = srows[0];
      parts.season = { name: s.name, status: seasons.seasonStatus(s), ends_at: s.ends_at, starts_at: s.starts_at };
    }
  } catch (e) { /* no season */ }
  return buildToday(parts);
}

module.exports = { buildToday, fetchToday };
