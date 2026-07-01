/**
 * Per-user trade performance analytics (pure, in-process).
 *
 * Given a user's closed trades, derive:
 *   - by_symbol : realised net-pnl / win-rate per symbol
 *   - drawdown  : max peak-to-trough drop of the cumulative realised-pnl curve
 *   - best/worst: single best and worst trade by pnl
 *   - expectancy: average pnl per closed trade
 *
 * Pure (no DB / I-O) so it runs identically over MySQL and the in-memory mock
 * and is unit-testable on its own. Trades are expected in ANY order; the curve
 * is built in chronological order using `closed_at` when present.
 */

function round2(n) { return Math.round(n * 100) / 100; }
function round1(n) { return Math.round(n * 10) / 10; }

// Max drawdown of the cumulative realised-pnl curve, in absolute $ and as a
// percent of the running peak equity (startEquity + peak cumulative pnl).
function _drawdown(chronoPnls, startEquity) {
  let cum = 0, peak = 0, maxAbs = 0, maxPct = 0;
  for (const p of chronoPnls) {
    cum += p;
    if (cum > peak) peak = cum;
    const dd = peak - cum;
    if (dd > maxAbs) {
      maxAbs = dd;
      const peakEquity = startEquity + peak;
      maxPct = peakEquity > 0 ? (dd / peakEquity) * 100 : 0;
    }
  }
  return { max_abs: round2(maxAbs), max_pct: round1(maxPct) };
}

function computePerformance(trades, { startEquity = 10000, top = 20 } = {}) {
  const rows = (trades || []).filter(t => t && t.pnl != null && Number.isFinite(Number(t.pnl)));

  // Chronological order for the drawdown curve (undefined closed_at sorts last-stable).
  const chrono = [...rows].sort((a, b) => {
    const ta = a.closed_at ? new Date(a.closed_at).getTime() : 0;
    const tb = b.closed_at ? new Date(b.closed_at).getTime() : 0;
    return ta - tb;
  });

  const bySymbol = new Map();
  let net = 0, best = null, worst = null;
  for (const t of rows) {
    const pnl = Number(t.pnl);
    net += pnl;
    if (best === null || pnl > best.pnl) best = { symbol: t.symbol, pnl: round2(pnl) };
    if (worst === null || pnl < worst.pnl) worst = { symbol: t.symbol, pnl: round2(pnl) };
    const key = t.symbol || '(unknown)';
    const g = bySymbol.get(key) || { symbol: key, n: 0, wins: 0, net_pnl: 0 };
    g.n += 1; if (pnl > 0) g.wins += 1; g.net_pnl += pnl;
    bySymbol.set(key, g);
  }

  const by_symbol = [...bySymbol.values()]
    .map(g => ({
      symbol: g.symbol, n: g.n, wins: g.wins, losses: g.n - g.wins,
      win_rate: g.n > 0 ? round1((g.wins / g.n) * 100) : 0,
      net_pnl: round2(g.net_pnl),
    }))
    .sort((a, b) => b.net_pnl - a.net_pnl)
    .slice(0, top);

  return {
    trades: rows.length,
    net_pnl: round2(net),
    expectancy: rows.length ? round2(net / rows.length) : 0,
    drawdown: _drawdown(chrono.map(t => Number(t.pnl)), startEquity),
    best, worst,
    by_symbol,
  };
}

module.exports = { computePerformance };
