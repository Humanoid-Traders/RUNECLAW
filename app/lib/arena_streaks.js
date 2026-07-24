'use strict';
/**
 * Arena streaks & weekly quests — gamification derived ONLY from real
 * closed-trade facts. Nothing is stored, granted, or invented: every number
 * recomputes from the trade history, so it can never drift from the truth.
 *
 * Streak: consecutive UTC days with at least one closed trade, counting
 * back from today — with a grace rule: a streak whose last close was
 * YESTERDAY is still "current" (today isn't over), it just doesn't count
 * today until a close lands.
 *
 * Quests: three per ISO week, rotated deterministically from the pool by
 * week number (same week → same quests for everyone — a shared arena,
 * no server state). Progress is computed from the CURRENT week's closes.
 *
 * §4: counts and percent only — these travel on private account payloads
 * and (streak count only) the public trader card.
 */

const DAY_MS = 86400000;

const utcDay = (d) => Math.floor(new Date(d).getTime() / DAY_MS);

/** {current, best, active_today} from closed trades (any order). */
function computeStreak(trades, now = new Date()) {
  const days = new Set();
  for (const t of trades || []) {
    if (t && t.closed_at) days.add(utcDay(t.closed_at));
  }
  if (!days.size) return { current: 0, best: 0, active_today: false };
  const today = utcDay(now);
  // Best: longest run anywhere in history.
  const sorted = [...days].sort((a, b) => a - b);
  let best = 1, run = 1;
  for (let i = 1; i < sorted.length; i++) {
    run = sorted[i] === sorted[i - 1] + 1 ? run + 1 : 1;
    if (run > best) best = run;
  }
  // Current: run ending today (or yesterday — today isn't over yet).
  let anchor = days.has(today) ? today : (days.has(today - 1) ? today - 1 : null);
  let current = 0;
  while (anchor != null && days.has(anchor)) { current++; anchor--; }
  return { current, best, active_today: days.has(today) };
}

// The quest pool. Each: deterministic progress from ONE week's closes.
const QUEST_POOL = [
  { key: 'five_closes', icon: '🎯', name: 'Close 5 trades', target: 5,
    have: (w) => w.length },
  { key: 'three_tp', icon: '🏹', name: 'Land 3 take-profit exits', target: 3,
    have: (w) => w.filter((t) => t.reason === 'tp').length },
  { key: 'three_symbols', icon: '🧭', name: 'Trade 3 different symbols', target: 3,
    have: (w) => new Set(w.map((t) => t.symbol)).size },
  { key: 'three_wins', icon: '🏆', name: 'Win 3 trades', target: 3,
    have: (w) => w.filter((t) => Number(t.pnl) > 0).length },
  { key: 'survive_zero', icon: '🛡️', name: 'A week with zero liquidations (min 3 closes)', target: 1,
    have: (w) => (w.length >= 3 && !w.some((t) => t.reason === 'liquidated')) ? 1 : 0 },
  { key: 'planned_exit', icon: '📐', name: 'Close 2 trades by your own TP or SL', target: 2,
    have: (w) => w.filter((t) => t.reason === 'tp' || t.reason === 'sl').length },
];

/** ISO week number (UTC) — the deterministic rotation key. */
function isoWeek(now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - day);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  return Math.ceil(((d - yearStart) / DAY_MS + 1) / 7);
}

/** Start of the current ISO week (Monday 00:00 UTC). */
function weekStart(now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const day = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - (day - 1));
  return d;
}

/** Three quests for this week with progress from THIS week's closes. */
function weeklyQuests(trades, now = new Date()) {
  const start = weekStart(now).getTime();
  const week = (trades || []).filter(
    (t) => t && t.closed_at && new Date(t.closed_at).getTime() >= start);
  const rot = isoWeek(now) + new Date(now).getUTCFullYear();
  const picks = [];
  for (let i = 0; i < 3; i++) picks.push(QUEST_POOL[(rot + i * 2) % QUEST_POOL.length]);
  return picks.map((q) => {
    const have = Math.min(q.target, q.have(week));
    return { key: q.key, icon: q.icon, name: q.name, target: q.target, have, done: have >= q.target };
  });
}

module.exports = { computeStreak, weeklyQuests, isoWeek, weekStart, QUEST_POOL };
