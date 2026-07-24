'use strict';
/**
 * Arena competition seasons — pure helpers behind /api/arena/season.
 *
 * A season is a NAMED TIME WINDOW over the existing Arena, never a reset:
 * the season board ranks percent return from trades CLOSED inside the window
 * (realized pnl vs the uniform starting stake). No account wipes, no
 * reset-gaming (you can't erase a bad season by re-provisioning), and the
 * all-time board keeps running alongside.
 *
 * §4: the public season payload carries opt-in handles + percent only.
 */

const { START_BALANCE } = require('./arena');

const NAME_RE = /^[\w --·:'!?]{3,60}$/;

/** 'upcoming' | 'live' | 'ended' at `now`. */
function seasonStatus(season, now) {
  const t = now instanceof Date ? now.getTime() : Number(now);
  const s = new Date(season.starts_at).getTime();
  const e = new Date(season.ends_at).getTime();
  if (!(t >= s)) return 'upcoming';
  return t < e ? 'live' : 'ended';
}

/** Validate an operator-authored season. */
function validateSeason(input) {
  const b = input || {};
  const name = String(b.name || '').trim();
  if (!NAME_RE.test(name)) return { ok: false, error: 'name must be 3–60 plain characters' };
  const starts = new Date(b.starts_at), ends = new Date(b.ends_at);
  if (isNaN(starts) || isNaN(ends)) return { ok: false, error: 'starts_at and ends_at must be dates' };
  if (ends <= starts) return { ok: false, error: 'the season must end after it starts' };
  const days = (ends - starts) / 86400000;
  if (days > 92) return { ok: false, error: 'a season runs at most ~3 months' };
  return { ok: true, data: { name, starts_at: starts, ends_at: ends } };
}

/**
 * Rank in-window realized performance. `trades` are arena_trades rows closed
 * inside the window; `handleOf` maps user_id → opt-in handle. Only handled
 * users appear (same privacy model as every public board).
 */
function seasonRanking(trades, handleOf) {
  const byUser = new Map();
  for (const t of trades || []) {
    if (!byUser.has(t.user_id)) byUser.set(t.user_id, { pnl: 0, n: 0 });
    const u = byUser.get(t.user_id);
    u.pnl += Number(t.pnl) || 0;
    u.n += 1;
  }
  const rows = [];
  for (const [userId, u] of byUser) {
    const handle = handleOf.get ? handleOf.get(userId) : handleOf[userId];
    if (!handle) continue;
    rows.push({
      handle,
      return_pct: Math.round(u.pnl / START_BALANCE * 10000) / 100,
      trades: u.n,
    });
  }
  rows.sort((a, b) => b.return_pct - a.return_pct);
  return rows.slice(0, 50).map((r, i) => ({ rank: i + 1, ...r }));
}

module.exports = { seasonStatus, validateSeason, seasonRanking, NAME_RE };
