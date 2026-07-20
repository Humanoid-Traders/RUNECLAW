'use strict';
/**
 * Portfolio intelligence (PR FF) — deterministic analytics computed ONLY from
 * recorded closed trades. No external price history is ever fetched: the
 * buy-and-hold benchmark for each trade is reconstructed from that trade's own
 * recorded entry/exit prices over its own holding window, so every figure is
 * re-derivable from the same rows the track record publishes.
 *
 * Per-trade alpha vs holding:
 *   agent return   = pnl / size_usd            (net of fees, on notional)
 *   holding return = (exit - entry) / entry    (what buying-and-holding the
 *                                               asset over the same window did)
 *   alpha          = agent - holding
 *
 * A SHORT in a falling market shows a negative holding return and a positive
 * agent return — large positive alpha, which is exactly the question this
 * answers: did the agent beat simply holding what it traded? Rows missing a
 * usable price or notional are skipped and COUNTED, never guessed at.
 */

const { pool } = require('../db');

function round2(v) { return Math.round(v * 100) / 100; }

/**
 * Pure. `trades`: closed-trade rows in chronological (closed_at ASC) order,
 * each with pnl, size_usd and optionally entry_price/exit_price/symbol.
 */
function computeIntel(trades) {
  let wins = 0, lossCount = 0, grossWin = 0, grossLoss = 0;
  let cum = 0, peak = 0, maxDdUsd = 0;
  let winStreak = 0, lossStreak = 0, bestWinStreak = 0, bestLossStreak = 0;
  let counted = 0, skipped = 0;

  // Alpha accumulators (only rows with usable prices participate).
  let priced = 0, alphaSum = 0, beat = 0;
  let bestAlpha = null, worstAlpha = null;

  for (const t of trades || []) {
    const pnl = parseFloat(t.pnl);
    const size = parseFloat(t.size_usd);
    if (!isFinite(pnl) || !isFinite(size) || size <= 0) { skipped++; continue; }
    counted++;

    if (pnl > 0) {
      wins++; grossWin += pnl;
      winStreak++; lossStreak = 0;
      bestWinStreak = Math.max(bestWinStreak, winStreak);
    } else if (pnl < 0) {
      lossCount++; grossLoss += -pnl;
      lossStreak++; winStreak = 0;
      bestLossStreak = Math.max(bestLossStreak, lossStreak);
    } else {
      winStreak = 0; lossStreak = 0;
    }

    // Realized drawdown on the cumulative-PnL curve (dollar terms — this
    // figure stays on PRIVATE surfaces; public compositions use percent-only
    // fields, never this one).
    cum += pnl;
    peak = Math.max(peak, cum);
    maxDdUsd = Math.max(maxDdUsd, peak - cum);

    const entry = parseFloat(t.entry_price);
    const exit = parseFloat(t.exit_price);
    if (isFinite(entry) && entry > 0 && isFinite(exit) && exit > 0) {
      const agentPct = pnl / size * 100;
      const holdPct = (exit - entry) / entry * 100;
      const alphaPct = agentPct - holdPct;
      priced++;
      alphaSum += alphaPct;
      if (alphaPct > 0) beat++;
      const sym = String(t.symbol || '').split('/')[0];
      if (!bestAlpha || alphaPct > bestAlpha.alpha_pct) bestAlpha = { symbol: sym, alpha_pct: round2(alphaPct) };
      if (!worstAlpha || alphaPct < worstAlpha.alpha_pct) worstAlpha = { symbol: sym, alpha_pct: round2(alphaPct) };
    }
  }

  const avgWin = wins ? grossWin / wins : null;
  const avgLoss = lossCount ? grossLoss / lossCount : null;

  return {
    trades: counted,
    skipped,
    wins,
    losses: lossCount,
    win_rate_pct: counted ? round2(wins / counted * 100) : null,
    net_pnl_usd: round2(cum),
    expectancy_usd: counted ? round2(cum / counted) : null,
    avg_win_usd: avgWin !== null ? round2(avgWin) : null,
    avg_loss_usd: avgLoss !== null ? round2(avgLoss) : null,
    payoff_ratio: (avgWin !== null && avgLoss !== null && avgLoss > 0) ? round2(avgWin / avgLoss) : null,
    profit_factor: grossLoss > 0 ? round2(grossWin / grossLoss) : null,
    max_drawdown_usd: round2(maxDdUsd),
    longest_win_streak: bestWinStreak,
    longest_loss_streak: bestLossStreak,
    alpha: priced ? {
      priced,
      unpriced: counted - priced,
      mean_alpha_pct: round2(alphaSum / priced),
      beat_market: beat,
      beat_market_pct: round2(beat / priced * 100),
      best: bestAlpha,
      worst: worstAlpha,
    } : null,
  };
}

/** Load a user's full closed history (oldest first) with the price columns. */
async function loadIntelTrades(userId) {
  const [rows] = await pool.execute(
    `SELECT symbol, direction, entry_price, exit_price, pnl, fees, size_usd, opened_at, closed_at
       FROM trades
      WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
      ORDER BY closed_at ASC`, [userId]);
  return rows;
}

async function getUserIntel(userId) {
  return computeIntel(await loadIntelTrades(userId));
}

module.exports = { computeIntel, loadIntelTrades, getUserIntel };
