/**
 * Continuous Tax & Compliance Agent — pure realized-gains math.
 *
 * RUNECLAW trades are discrete round-trip positions: every closed trade is a
 * self-contained disposal with its own entry, exit, and booked realized PnL.
 * There is no spot-lot ledger to match across, so forcing FIFO cost-basis
 * matching onto already-matched round-trips would MISREPRESENT the data. The
 * honest model is therefore one disposal per closed trade — realized gain/loss
 * = the engine-booked pnl, holding period = opened_at → closed_at, and a
 * short/long-term split at the conventional 365-day line.
 *
 * This is INFORMATIONAL ONLY. It is a starting point for a qualified tax
 * professional, never advice, a filing, or a verdict (§4: heuristic flags,
 * never verdicts). All functions here are pure and deterministic so the math
 * can be unit-tested object-in / dict-out.
 */

'use strict';

const MS_PER_DAY = 86400000;
const LONG_TERM_DAYS = 365; // conventional short/long-term boundary

const DISCLAIMER =
  'Informational only — not tax advice. RUNECLAW trades are primarily perpetual ' +
  'futures / derivatives, which many jurisdictions tax differently from spot ' +
  'capital gains (e.g. as ordinary income, or under special regimes such as US ' +
  'IRC §1256 mark-to-market). This report summarises realized round-trip results ' +
  'from your own trade history as a starting point for a qualified tax ' +
  'professional — it is not a filing, a verdict, or advice. Always reconcile ' +
  'every figure against your exchange statements before relying on it.';

function num(v) {
  const n = typeof v === 'number' ? v : parseFloat(v);
  return Number.isFinite(n) ? n : 0;
}

function round2(v) {
  return Math.round((num(v) + Number.EPSILON) * 100) / 100;
}

function validDate(dateLike) {
  if (dateLike == null) return null;
  const d = new Date(dateLike);
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * Classify a single closed trade into a disposal (tax-lot) row.
 * @param {object} trade row with symbol, direction, size_usd, pnl, fees,
 *                        opened_at, closed_at.
 */
function classifyDisposal(trade) {
  const acquired = validDate(trade.opened_at);
  const disposed = validDate(trade.closed_at);

  const gain = num(trade.pnl);           // realized, as booked by the engine
  const fees = num(trade.fees);
  const costBasis = num(trade.size_usd); // capital committed at entry (notional)
  const proceeds = costBasis + gain;     // identity: proceeds − basis = realized gain

  let holdingDays = null;
  let term = 'unknown';
  if (acquired && disposed) {
    holdingDays = Math.max(0, Math.round((disposed.getTime() - acquired.getTime()) / MS_PER_DAY));
    term = holdingDays >= LONG_TERM_DAYS ? 'long' : 'short';
  }

  return {
    symbol: String(trade.symbol || ''),
    direction: String(trade.direction || ''),
    acquired: acquired ? acquired.toISOString() : null,
    disposed: disposed ? disposed.toISOString() : null,
    holding_days: holdingDays,
    term,
    cost_basis: round2(costBasis),
    proceeds: round2(proceeds),
    fees: round2(fees),
    gain_loss: round2(gain),
    year: disposed ? disposed.getUTCFullYear() : null,
  };
}

function emptyYear(year) {
  return {
    year,
    disposals: 0,
    gains: 0,
    losses: 0,
    proceeds: 0,
    cost_basis: 0,
    fees: 0,
    net_gain_loss: 0,
    short_term_gain_loss: 0,
    long_term_gain_loss: 0,
  };
}

/**
 * Aggregate disposal rows into per-year summaries (descending by year) plus a
 * grand total across every year present.
 */
function summarize(disposals) {
  const byYear = new Map();
  const totals = emptyYear('all');

  for (const d of disposals) {
    if (d.year == null) continue;
    if (!byYear.has(d.year)) byYear.set(d.year, emptyYear(d.year));
    const y = byYear.get(d.year);
    for (const bucket of [y, totals]) {
      bucket.disposals += 1;
      if (d.gain_loss > 0) bucket.gains += 1;
      else if (d.gain_loss < 0) bucket.losses += 1;
      bucket.proceeds = round2(bucket.proceeds + d.proceeds);
      bucket.cost_basis = round2(bucket.cost_basis + d.cost_basis);
      bucket.fees = round2(bucket.fees + d.fees);
      bucket.net_gain_loss = round2(bucket.net_gain_loss + d.gain_loss);
      if (d.term === 'long') bucket.long_term_gain_loss = round2(bucket.long_term_gain_loss + d.gain_loss);
      else bucket.short_term_gain_loss = round2(bucket.short_term_gain_loss + d.gain_loss);
    }
  }

  const years = Array.from(byYear.values()).sort((a, b) => b.year - a.year);
  return { years, totals };
}

/**
 * Build the full report from raw closed-trade rows.
 * @param {Array<object>} trades closed-trade rows.
 * @param {object} [opts] { year } — restrict to a single tax year.
 */
function buildReport(trades, opts = {}) {
  const disposals = (Array.isArray(trades) ? trades : [])
    .map(classifyDisposal)
    .filter((d) => d.disposed != null);

  const year = opts.year != null && Number.isInteger(Number(opts.year)) ? Number(opts.year) : null;
  const scoped = year != null ? disposals.filter((d) => d.year === year) : disposals;
  scoped.sort((a, b) => new Date(b.disposed) - new Date(a.disposed));

  const { years, totals } = summarize(scoped);
  return {
    scope: year != null ? String(year) : 'all',
    available_years: Array.from(new Set(disposals.map((d) => d.year))).sort((a, b) => b - a),
    years,
    totals,
    disposals: scoped,
    disclaimer: DISCLAIMER,
  };
}

function csvCell(v) {
  const s = v == null ? '' : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Form-8949-friendly CSV of the disposal rows (headers + one line per disposal).
 */
function toCsv(disposals) {
  const header = [
    'Symbol', 'Direction', 'Date Acquired', 'Date Sold',
    'Proceeds (USD)', 'Cost Basis (USD)', 'Fees (USD)',
    'Gain/Loss (USD)', 'Holding Days', 'Term',
  ];
  const lines = [header.join(',')];
  for (const d of disposals || []) {
    lines.push([
      d.symbol, d.direction,
      d.acquired || '', d.disposed || '',
      d.proceeds, d.cost_basis, d.fees, d.gain_loss,
      d.holding_days == null ? '' : d.holding_days, d.term,
    ].map(csvCell).join(','));
  }
  return lines.join('\n') + '\n';
}

module.exports = {
  DISCLAIMER,
  LONG_TERM_DAYS,
  classifyDisposal,
  summarize,
  buildReport,
  toCsv,
};
