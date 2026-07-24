'use strict';
/**
 * Season ceremony watch — the two moments a competition lives for announce
 * themselves: the STARTING GUN (upcoming → live) and the FINAL WHISTLE
 * (live → ended, crowning the champion). The authoring announcement fires
 * when the operator launches; this watch covers the transitions that happen
 * later, while nobody is clicking anything.
 *
 * Durable dedupe: announced_live / announced_end flags on the season row are
 * flipped BEFORE the fanfare, so a restart or a second replica never
 * re-announces. Announcements are best-effort (push + public mind-stream
 * event) and never throw. §4: names, windows and handles only — no numbers.
 */

const { pool } = require('../db');
const { seasonStatus, seasonRanking } = require('./arena_seasons');

/** Pure: which ceremonies are due at `now`. */
function transitions(seasons, now) {
  const due = [];
  for (const s of seasons || []) {
    const st = seasonStatus(s, now);
    if (st !== 'upcoming' && !Number(s.announced_live)) due.push({ season: s, kind: 'live' });
    if (st === 'ended' && !Number(s.announced_end)) due.push({ season: s, kind: 'ended' });
  }
  return due;
}

async function announce(kind, season) {
  const push = require('./push');
  let title, body;
  if (kind === 'live') {
    title = `🏁 ${season.name} is LIVE`;
    body = 'The season has started — every trade you close inside the window counts. Take the crown.';
  } else {
    // Crown the champion when one exists (handle only — §4).
    let champ = null;
    try {
      const [trades] = await pool.execute(
        'SELECT user_id, pnl FROM arena_trades WHERE closed_at >= ? AND closed_at <= ?',
        [season.starts_at, season.ends_at]);
      const [handles] = await pool.execute(
        'SELECT id, leaderboard_handle FROM users WHERE leaderboard_handle IS NOT NULL');
      const rows = seasonRanking(trades, new Map(handles.map((h) => [h.id, h.leaderboard_handle])));
      champ = rows[0] ? rows[0].handle : null;
    } catch (e) { /* no champion data — announce without one */ }
    title = `🏆 ${season.name} — final whistle`;
    body = champ
      ? `The season has ended — the crown goes to ${champ}. Final standings are sealed in the Hall of Champions.`
      : 'The season has ended — the crown went unclaimed. The Hall of Champions records it all.';
  }
  try {
    await push.notifySubscribers({ title, body, url: '/arena' });
  } catch (e) { /* push not configured */ }
  try {
    await pool.execute(
      `INSERT INTO agent_events (event_type, severity, symbol, title, body, data_json, created_at)
       VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ['arena_season', 'info', null, title, body,
        JSON.stringify({ kind, starts_at: season.starts_at, ends_at: season.ends_at }), new Date()]);
  } catch (e) { /* feed insert is best-effort */ }
}

async function runOnce(now = new Date()) {
  const [seasons] = await pool.execute(
    'SELECT id, name, starts_at, ends_at, announced_live, announced_end FROM arena_seasons');
  for (const t of transitions(seasons, now)) {
    // Flip the flag FIRST — a crash mid-announce must not replay the ceremony.
    const col = t.kind === 'live' ? 'announced_live' : 'announced_end';
    await pool.execute(`UPDATE arena_seasons SET ${col} = ? WHERE id = ?`, [1, t.season.id]);
    await announce(t.kind, t.season);
  }
}

let timer = null;
function startSeasonWatch(intervalMs = 60_000) {
  if (timer) return;
  timer = setInterval(() => { runOnce().catch(() => {}); }, intervalMs);
  if (timer.unref) timer.unref();
}

module.exports = { startSeasonWatch, runOnce, transitions };
