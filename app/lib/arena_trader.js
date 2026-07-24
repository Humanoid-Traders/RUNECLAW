'use strict';
/**
 * Public Arena trader card — the §4-safe PUBLIC view of one opted-in handle.
 *
 * Everything here is percent / count / badge — NEVER an amount, not even a
 * virtual one: per-trade performance is return-on-margin percent, the account
 * line is percent return vs the uniform stake. Pure builder so the §4 shape
 * is exactly testable.
 */

const arena = require('./arena');
const { computeArenaBadges } = require('./arena_badges');

const HANDLE_RE = /^[A-Za-z0-9_]{3,20}$/;

/**
 * @param {object} ctx
 *   handle, balance, positions[], marks, trades[] (newest first, full rows)
 * @returns the public payload (no user_id, no balances, no vUSDT amounts)
 */
function buildTraderCard(ctx) {
  const trades = ctx.trades || [];
  const eq = arena.equity(ctx.balance, ctx.positions || [], ctx.marks || {});
  const returnPct = arena.returnPct(eq);
  const wins = trades.filter((t) => Number(t.pnl) > 0).length;
  const badges = computeArenaBadges({ trades, returnPct })
    .filter((b) => b.earned)
    .map((b) => ({ key: b.key, icon: b.icon, name: b.name }));
  return {
    handle: ctx.handle,
    return_pct: Math.round(returnPct * 100) / 100,
    closed_trades: trades.length,
    open_positions: (ctx.positions || []).length,
    win_rate_pct: trades.length ? Math.round(wins / trades.length * 1000) / 10 : null,
    badges,
    recent: trades.slice(0, 15).map((t) => ({
      symbol: t.symbol,
      direction: t.direction,
      leverage: t.leverage,
      ret_pct: t.margin > 0 ? Math.round(Number(t.pnl) / Number(t.margin) * 10000) / 100 : null,
      reason: t.reason,
      closed_at: t.closed_at,
    })),
    virtual: true,
  };
}

/**
 * Fetch + build the card for a handle (or null when unknown / no account).
 * Used by both the public API and the /trader/:handle SSR unfurl, so the
 * share text and the page always agree.
 */
async function fetchTraderCard(handle) {
  if (!HANDLE_RE.test(String(handle || ''))) return null;
  const { pool } = require('../db');
  const { getTickers } = require('./tickers');
  const [u] = await pool.execute('SELECT id FROM users WHERE leaderboard_handle = ?', [handle]);
  if (!u[0]) return null;
  const userId = u[0].id;
  const [acct] = await pool.execute('SELECT user_id, balance FROM arena_accounts WHERE user_id = ?', [userId]);
  if (!acct[0]) return null;
  const [positions] = await pool.execute(
    'SELECT id, user_id, symbol, direction, entry, margin, leverage, source, tp, sl, opened_at FROM arena_positions WHERE user_id = ? ORDER BY id DESC', [userId]);
  const [trades] = await pool.execute(
    'SELECT id, symbol, direction, entry, exit_price, margin, leverage, pnl, reason, opened_at, closed_at FROM arena_trades WHERE user_id = ? ORDER BY id DESC LIMIT 30', [userId]);
  let marks = {};
  try { marks = await getTickers(); } catch (e) { /* percent renders from balance */ }
  return buildTraderCard({ handle, balance: acct[0].balance, positions, marks, trades });
}

module.exports = { buildTraderCard, fetchTraderCard, HANDLE_RE };
