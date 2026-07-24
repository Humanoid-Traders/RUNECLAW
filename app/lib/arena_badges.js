'use strict';
/**
 * Arena achievements — a small, honest gamification layer for the Paper
 * Trading Arena. Every badge is derived ONLY from verifiable arena facts
 * (closed trades, liquidations, symbols, percent return) — counts, streaks
 * and percents, never a dollar figure, so an earned badge is §4-safe to show
 * anywhere. Locked badges are returned too so users see what's left to earn.
 *
 * Pure & deterministic — trades-in / list-out (newest-first rows, as the
 * account endpoint already returns them).
 */

/**
 * @param {object} ctx
 *   trades     — closed arena_trades rows, NEWEST first ({ pnl, reason, symbol })
 *   returnPct  — current all-time percent return vs the uniform stake
 */
function computeArenaBadges(ctx = {}) {
  const trades = Array.isArray(ctx.trades) ? ctx.trades : [];
  const returnPct = Number(ctx.returnPct) || 0;
  const n = trades.length;
  const wins = trades.filter((t) => Number(t.pnl) > 0).length;
  const liqs = trades.filter((t) => t.reason === 'liquidated').length;
  const symbols = new Set(trades.map((t) => t.symbol)).size;

  // Streaks read oldest→newest so "3 in a row" means consecutive in time.
  const chrono = trades.slice().reverse();
  let bestWinStreak = 0, run = 0, comeback = false, lossRun = 0;
  for (const t of chrono) {
    if (Number(t.pnl) > 0) {
      run += 1; bestWinStreak = Math.max(bestWinStreak, run);
      if (lossRun >= 2) comeback = true;
      lossRun = 0;
    } else {
      run = 0; lossRun += 1;
    }
  }

  const planned = trades.filter((t) => t.reason === 'tp' || t.reason === 'sl').length;
  const targetsHit = trades.filter((t) => t.reason === 'tp').length;

  const defs = [
    ['first_blood', '🩸', 'First blood', 'Close your first paper trade.', n >= 1],
    ['planner', '📐', 'The planner', 'Have 5 trades close by your own TP or SL — exits decided before entry.', planned >= 5],
    ['target_hit', '🏹', 'Bullseye', 'Ride 3 trades all the way into your take-profit.', targetsHit >= 3],
    ['veteran', '🎖️', 'Arena veteran', 'Close 10 paper trades.', n >= 10],
    ['hot_streak', '🔥', 'Hot streak', 'Win 3 closes in a row.', bestWinStreak >= 3],
    ['sharpshooter', '🎯', 'Sharpshooter', 'Win rate 60%+ over 10+ closes.', n >= 10 && wins / n >= 0.6],
    ['iron_hands', '🛡️', 'Iron hands', '10+ closes without a single liquidation.', n >= 10 && liqs === 0],
    ['comeback', '💪', 'The comeback', 'Win a trade after two straight losses.', comeback],
    ['explorer', '🧭', 'Market explorer', 'Trade 3 different symbols.', symbols >= 3],
    ['in_the_green', '🌱', 'In the green', 'Hold a positive all-time return.', returnPct > 0],
    ['high_flyer', '🚀', 'High flyer', 'Reach +10% all-time return.', returnPct >= 10],
  ];

  return defs.map(([key, icon, name, desc, earned]) => ({ key, icon, name, desc, earned: !!earned }));
}

module.exports = { computeArenaBadges };
