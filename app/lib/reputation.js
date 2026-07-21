/**
 * Outcome-Based Agent Reputation (Guardian).
 *
 * A verifiable reputation READOUT computed only from an agent's own realized,
 * closed trades — not vanity metrics and not a lucky single run. It grades on
 * what actually happened: profitability quality, risk discipline, cost
 * efficiency, and consistency over time — then pulls the whole thing toward a
 * neutral 50 when the sample is too thin to trust (you can neither earn a high
 * reputation nor be condemned on three trades).
 *
 * ADVISORY ONLY — a heuristic score, never a verdict on whether to trust the
 * agent (§4). It is intentionally leverage-agnostic and dollar-free (every
 * metric is a ratio), so it reads honestly without inventing a starting
 * balance and stays shareable without exposing amounts.
 *
 * Honest scope: this is outcome-based on realized closed trades. It does NOT
 * verify mandate / declared-leverage compliance against a policy envelope —
 * that requires the Intent Compiler and is a noted follow-up. "Across regimes"
 * is approximated by month buckets, not a labelled regime model.
 *
 * Pure & deterministic so it runs identically over MySQL and the mock and is
 * unit-testable on its own.
 */

'use strict';

const { computePerformance } = require('./trade_performance');

function num(v) { const n = typeof v === 'number' ? v : parseFloat(v); return Number.isFinite(n) ? n : 0; }
function clamp(v, lo = 0, hi = 100) { return Math.max(lo, Math.min(hi, v)); }
function round1(n) { return Math.round(n * 10) / 10; }

// Piecewise-linear map through (x, y) knots (x ascending).
function lerpKnots(x, knots) {
  if (x <= knots[0][0]) return knots[0][1];
  for (let i = 1; i < knots.length; i++) {
    if (x <= knots[i][0]) {
      const [x0, y0] = knots[i - 1], [x1, y1] = knots[i];
      const t = x1 === x0 ? 0 : (x - x0) / (x1 - x0);
      return y0 + t * (y1 - y0);
    }
  }
  return knots[knots.length - 1][1];
}

function monthKey(dateLike) {
  const d = new Date(dateLike);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

function gradeOf(score) {
  if (score >= 85) return 'A';
  if (score >= 70) return 'B';
  if (score >= 55) return 'C';
  if (score >= 40) return 'D';
  return 'E';
}

// Confidence in the score, from sample size.
function sampleConfidence(n) {
  return Math.round(lerpKnots(n, [[0, 10], [5, 30], [10, 45], [20, 65], [50, 85], [100, 100]]));
}

/**
 * @param {Array<object>} trades closed-trade rows (symbol, direction, pnl,
 *        size_usd, fees, opened_at, closed_at).
 * @param {object} [opts] { startEquity } — for the drawdown %.
 */
function computeReputation(trades, opts = {}) {
  const rows = (Array.isArray(trades) ? trades : [])
    .filter(t => t && t.pnl != null && Number.isFinite(Number(t.pnl)));
  const n = rows.length;

  const note =
    'Advisory reputation readout — a heuristic score, never a verdict. Computed only ' +
    'from realized closed trades; it does not verify mandate or declared-leverage ' +
    'compliance against a policy envelope (that requires the Intent Compiler), and ' +
    'consistency is approximated by month buckets. Verify against exchange statements.';

  if (n === 0) {
    return {
      score: null, grade: null, unrated: true,
      subscores: { performance: null, risk_discipline: null, cost_efficiency: null, consistency: null },
      metrics: { trades: 0, win_rate: null, profit_factor: null, expectancy_r: null, max_drawdown_pct: null, fee_drag_pct: null, positive_months: 0, total_months: 0 },
      sample: { trades: 0, confidence: sampleConfidence(0) },
      flags: [{ key: 'no_trades', severity: 'info', label: 'No closed trades yet — reputation is unrated.' }],
      note,
    };
  }

  let grossWin = 0, grossLoss = 0, fees = 0, wins = 0, absPnl = 0, sumR = 0, rCount = 0, worstLoss = 0, avgWinAbs = 0;
  const byMonth = new Map();
  for (const t of rows) {
    const pnl = num(t.pnl);
    fees += num(t.fees);
    absPnl += Math.abs(pnl);
    if (pnl > 0) { grossWin += pnl; wins += 1; }
    else if (pnl < 0) { grossLoss += Math.abs(pnl); if (Math.abs(pnl) > worstLoss) worstLoss = Math.abs(pnl); }
    const size = num(t.size_usd);
    if (size > 0) { sumR += pnl / size; rCount += 1; }
    const mk = t.closed_at ? monthKey(t.closed_at) : null;
    if (mk) byMonth.set(mk, (byMonth.get(mk) || 0) + pnl);
  }
  avgWinAbs = wins > 0 ? grossWin / wins : 0;
  const net = grossWin - grossLoss;

  // Max drawdown of the cumulative R (return-on-size) curve, in R units. This is
  // size- and leverage-normalized, so the risk sub-score doesn't depend on an
  // externally-supplied equity figure the way a drawdown-% would.
  const chrono = [...rows].sort((a, b) => {
    const ta = a.closed_at ? new Date(a.closed_at).getTime() : 0;
    const tb = b.closed_at ? new Date(b.closed_at).getTime() : 0;
    return ta - tb;
  });
  let cumR = 0, peakR = 0, maxDrawdownR = 0;
  for (const t of chrono) {
    const size = num(t.size_usd);
    if (size <= 0) continue;
    cumR += num(t.pnl) / size;
    if (cumR > peakR) peakR = cumR;
    const dd = peakR - cumR;
    if (dd > maxDrawdownR) maxDrawdownR = dd;
  }

  const win_rate = round1((wins / n) * 100);
  const profit_factor = grossLoss > 0 ? grossWin / grossLoss : (grossWin > 0 ? Infinity : 0);
  const expectancy_r = rCount > 0 ? sumR / rCount : 0;
  const fee_drag_pct = absPnl > 0 ? round1((fees / absPnl) * 100) : 0;

  const perf = computePerformance(rows, { startEquity: num(opts.startEquity) || 10000 });
  const max_drawdown_pct = perf.drawdown.max_pct;

  const months = [...byMonth.values()];
  const total_months = months.length;
  const positive_months = months.filter(v => v > 0).length;
  const positive_period_ratio = total_months > 0 ? positive_months / total_months : 0;

  // ── Sub-scores (0–100) ──
  const pfScore = lerpKnots(profit_factor === Infinity ? 3 : profit_factor,
    [[0, 0], [0.5, 5], [1, 45], [1.3, 65], [1.8, 85], [2.5, 100]]);
  const performance = clamp(pfScore * 0.6 + clamp(win_rate) * 0.4);

  let risk_discipline = lerpKnots(maxDrawdownR, [[0, 100], [0.5, 85], [1, 68], [2, 45], [4, 18], [8, 8]]);
  // Blow-up penalty: a single loss dwarfing the average win.
  const blowup = avgWinAbs > 0 && worstLoss > 3 * avgWinAbs;
  if (blowup) risk_discipline = clamp(risk_discipline - 20);
  risk_discipline = clamp(risk_discipline);

  const cost_efficiency = clamp(lerpKnots(fee_drag_pct, [[0, 100], [5, 85], [12, 65], [25, 45], [50, 15], [100, 5]]));

  // Consistency needs at least a couple of months to mean anything; below that,
  // lean on win_rate at reduced weight rather than fabricate a period signal.
  const consistency = total_months >= 2
    ? clamp(positive_period_ratio * 100)
    : clamp(win_rate * 0.7 + 15);

  const base = performance * 0.35 + risk_discipline * 0.25 + cost_efficiency * 0.15 + consistency * 0.25;
  const confidence = sampleConfidence(n);
  // Pull toward neutral 50 when the sample is thin — earned reputation, not luck.
  let score = Math.round(clamp(50 + (base - 50) * (confidence / 100)));
  // A net-losing agent cannot rate above neutral, however clean the other
  // dimensions look — the outcome is the point.
  if (net <= 0) score = Math.min(score, 45);

  const flags = [];
  if (n < 20) flags.push({ key: 'thin_sample', severity: 'warn', label: `Thin sample (${n} trades) — the score is pulled toward neutral until there's more history.` });
  if (net <= 0) flags.push({ key: 'unprofitable', severity: 'bad', label: 'Net-negative over this window.' });
  if (max_drawdown_pct > 25) flags.push({ key: 'high_drawdown', severity: 'warn', label: `Deep drawdown (${max_drawdown_pct}% of peak).` });
  if (fee_drag_pct > 20) flags.push({ key: 'high_fee_drag', severity: 'warn', label: `Fees consume ${fee_drag_pct}% of total P&L activity.` });
  if (total_months >= 3 && positive_period_ratio < 0.4) flags.push({ key: 'inconsistent', severity: 'warn', label: `Only ${positive_months}/${total_months} months positive.` });
  if (blowup) flags.push({ key: 'blowup_risk', severity: 'bad', label: 'A single loss dwarfs the average win — tail-risk exposure.' });
  if (!flags.length) flags.push({ key: 'clean', severity: 'good', label: 'No reputation red flags over this window.' });

  return {
    score, grade: gradeOf(score), unrated: false,
    subscores: {
      performance: Math.round(performance),
      risk_discipline: Math.round(risk_discipline),
      cost_efficiency: Math.round(cost_efficiency),
      consistency: Math.round(consistency),
    },
    metrics: {
      trades: n,
      win_rate,
      profit_factor: profit_factor === Infinity ? null : round1(profit_factor),
      expectancy_r: Math.round(expectancy_r * 10000) / 10000,
      max_drawdown_pct,
      fee_drag_pct,
      positive_months, total_months,
    },
    sample: { trades: n, confidence },
    flags,
    note,
  };
}

module.exports = { computeReputation, sampleConfidence, gradeOf };
