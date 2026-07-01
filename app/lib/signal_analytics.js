/**
 * Signal performance analytics (pure, in-process aggregation).
 *
 * Given a list of resolved signal rows (each with a numeric `pnl`), break the
 * win-rate / net-pnl down by pattern, symbol, direction, and confidence bucket.
 * Kept as a pure function (no DB, no I/O) so it runs identically over the MySQL
 * pool and the in-memory mock, and is unit-testable on its own.
 *
 * A signal is a "win" when pnl > 0. Rows with a null/undefined/non-finite pnl
 * are treated as unresolved and ignored.
 */

// Confidence buckets: [label, lo, hi) with hi exclusive except the last.
const CONF_BUCKETS = [
  ['<50%', 0, 0.5],
  ['50-60%', 0.5, 0.6],
  ['60-70%', 0.6, 0.7],
  ['70-80%', 0.7, 0.8],
  ['80-90%', 0.8, 0.9],
  ['90%+', 0.9, 1.0001],
];

function bucketFor(conf) {
  const c = Number(conf) || 0;
  for (const [label, lo, hi] of CONF_BUCKETS) {
    if (c >= lo && c < hi) return label;
  }
  return CONF_BUCKETS[CONF_BUCKETS.length - 1][0];
}

function round1(n) { return Math.round(n * 10) / 10; }
function round2(n) { return Math.round(n * 100) / 100; }

// Accumulate one signal into a group map keyed by `key`.
function _add(map, key, isWin, pnl) {
  if (key == null || key === '') key = '(none)';
  const g = map.get(key) || { key, n: 0, wins: 0, net_pnl: 0 };
  g.n += 1;
  if (isWin) g.wins += 1;
  g.net_pnl += pnl;
  map.set(key, g);
}

// Finalise a group map into a win_rate-annotated array, sorted by sample count
// desc (most-traded first), capped at `top`.
function _finalise(map, top = 12) {
  return [...map.values()]
    .map(g => ({
      key: g.key,
      n: g.n,
      wins: g.wins,
      losses: g.n - g.wins,
      win_rate: g.n > 0 ? round1((g.wins / g.n) * 100) : 0,
      net_pnl: round2(g.net_pnl),
    }))
    .sort((a, b) => b.n - a.n)
    .slice(0, top);
}

function computeAnalytics(signals, { top = 12 } = {}) {
  const byPattern = new Map();
  const bySymbol = new Map();
  const byDirection = new Map();
  const byConfidence = new Map();
  let n = 0, wins = 0, net = 0;

  for (const s of signals || []) {
    const pnl = Number(s.pnl);
    if (s.pnl == null || !Number.isFinite(pnl)) continue; // unresolved
    const isWin = pnl > 0;
    n += 1; if (isWin) wins += 1; net += pnl;
    _add(byPattern, s.pattern, isWin, pnl);
    _add(bySymbol, s.symbol, isWin, pnl);
    _add(byDirection, (s.direction || '').toUpperCase(), isWin, pnl);
    _add(byConfidence, bucketFor(s.confidence), isWin, pnl);
  }

  // Confidence buckets keep their natural order (not by count).
  const confOrder = CONF_BUCKETS.map(b => b[0]);
  const byConfidenceArr = _finalise(byConfidence, confOrder.length)
    .sort((a, b) => confOrder.indexOf(a.key) - confOrder.indexOf(b.key));

  return {
    overall: {
      resolved: n,
      wins,
      losses: n - wins,
      win_rate: n > 0 ? round1((wins / n) * 100) : 0,
      net_pnl: round2(net),
    },
    by_pattern: _finalise(byPattern, top),
    by_symbol: _finalise(bySymbol, top),
    by_direction: _finalise(byDirection, 4),
    by_confidence: byConfidenceArr,
  };
}

module.exports = { computeAnalytics, bucketFor, CONF_BUCKETS };
