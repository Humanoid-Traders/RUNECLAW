/**
 * Guardian Readiness Score — one honest number for "is my agent safely
 * constrained right now?", composed ONLY from signals RUNECLAW already produces.
 *
 * The AI proposes, deterministic controls authorize, the wallet enforces, the
 * recorder proves, the escape agent recovers — this score reads how much of that
 * safety machinery is actually in place, across six independent axes, and names
 * the weakest link. It is a heuristic set of FLAGS, never a verdict or guarantee
 * (§4): a high score is not a promise of safety.
 *
 * This module is PURE and side-effect-free: the route gathers each raw signal
 * fail-soft (any missing one arrives as null → scored "not yet observed", never
 * silently 100) and hands them here. No dollar amounts are emitted — the score
 * is percentages and 0–100 sub-scores only, so it is safe to surface or share.
 */

'use strict';

// Reference drawdown band the headroom axis scores against (percent of peak).
const DRAWDOWN_REF_PCT = 25;

const AXES = [
  { key: 'envelope', label: 'Authority envelope', weight: 0.24,
    fix: { label: 'Tighten the envelope', href: '#guardian' } },
  { key: 'recorder', label: 'Flight-recorder integrity', weight: 0.16,
    fix: { label: 'Open the recorder', href: '#guardian' } },
  { key: 'drawdown', label: 'Drawdown headroom', weight: 0.20,
    fix: { label: 'Review performance', href: '#portfolio' } },
  { key: 'concentration', label: 'Position concentration', weight: 0.12,
    fix: { label: 'Review exposure', href: '#exposure' } },
  { key: 'counterparty', label: 'Counterparty spread', weight: 0.14,
    fix: { label: 'Review net worth', href: '#networth' } },
  { key: 'livegate', label: 'Live-exposure gate', weight: 0.14,
    fix: { label: 'Open controls', href: '#account' } },
];

const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
const r0 = (n) => Math.round(n);

const CAVEAT =
  'A heuristic safety read composed from RUNECLAW’s own live signals — '
  + 'a set of flags, never a verdict or guarantee. A high score is not a promise '
  + 'of safety; a missing signal is shown as "not yet observed", never as a pass.';

// ── Per-axis scorers: each returns { score: 0-100 | null, note } ──────────────

function scoreEnvelope(env) {
  if (!env) return { score: null, note: 'Authority status not yet observed.' };
  const mode = String(env.mode || 'off').toLowerCase();
  const bound = !!env.bound;
  if (mode === 'enforce') {
    return bound
      ? { score: 100, note: 'Enforce-mode envelope is authorizing every order.' }
      : { score: 70, note: 'Enforce mode is on but no envelope is bound yet.' };
  }
  if (mode === 'shadow' || mode === 'advisory') {
    return { score: 55, note: 'Envelope is advisory — it advises but does not block.' };
  }
  return bound
    ? { score: 35, note: 'An envelope exists but enforcement is off.' }
    : { score: 15, note: 'No enforcing envelope — the AI is not wallet-constrained.' };
}

function scoreRecorder(ok) {
  if (ok === null || ok === undefined) {
    return { score: null, note: 'No recorded decisions yet.' };
  }
  return ok
    ? { score: 100, note: 'Decision→outcome chain is intact.' }
    : { score: 0, note: 'Recorder chain shows a break — provenance is at risk.' };
}

function scoreDrawdown(ddPct) {
  if (ddPct === null || ddPct === undefined || !isFinite(ddPct)) {
    return { score: null, note: 'No closed trades to measure drawdown.' };
  }
  const score = clamp(r0(100 * (1 - ddPct / DRAWDOWN_REF_PCT)), 0, 100);
  return { score, note: `Max drawdown ${r0(ddPct)}% of a ${DRAWDOWN_REF_PCT}% reference band.` };
}

function scoreConcentration(topPct) {
  // topPct is 0..1 (top holding share of gross). Only meaningful with >=2 holdings;
  // the route passes null otherwise.
  if (topPct === null || topPct === undefined || !isFinite(topPct)) {
    return { score: null, note: 'Not enough open positions to measure concentration.' };
  }
  // Diversified (<=20%) → 100; dominated (>=80%) → 0.
  const score = clamp(r0(100 * (0.8 - topPct) / 0.6), 0, 100);
  return { score, note: `Top position is ${r0(topPct * 100)}% of gross exposure.` };
}

function scoreCounterparty(tier) {
  const map = { none: 100, low: 80, moderate: 50, high: 20 };
  if (!tier || !(tier in map)) {
    return { score: null, note: 'Counterparty mix not yet observed.' };
  }
  return { score: map[tier], note: `Counterparty concentration: ${tier}.` };
}

function scoreLivegate(live) {
  if (!live) return { score: null, note: 'Live-gate state not yet observed.' };
  if (live.paused) return { score: 100, note: 'De-risked — routed to paper / paused.' };
  if (live.live_enabled) {
    return live.allowlisted
      ? { score: 70, note: 'Live capital is exposed, operator-gated by the allowlist.' }
      : { score: 30, note: 'Live flag set without operator allowlist.' };
  }
  return { score: 100, note: 'Paper only — no live capital exposed.' };
}

const SCORERS = {
  envelope: (s) => scoreEnvelope(s.envelope),
  recorder: (s) => scoreRecorder(s.recorderOk),
  drawdown: (s) => scoreDrawdown(s.drawdownPct),
  concentration: (s) => scoreConcentration(s.concentrationPct),
  counterparty: (s) => scoreCounterparty(s.counterpartyTier),
  livegate: (s) => scoreLivegate(s.liveState),
};

function bandOf(score) {
  if (score === null) return 'unknown';
  if (score >= 80) return 'strong';
  if (score >= 60) return 'fair';
  return 'weak';
}

/**
 * Compose the readiness score from raw, already-gathered signals. Every input
 * is optional; any null is scored "not yet observed" and excluded from the
 * weighted total (weights re-normalise over the observed axes). If NOTHING is
 * observed the total is null (band "unknown"), never a spurious number.
 */
function scoreReadiness(signals = {}) {
  const subscores = AXES.map((ax) => {
    const { score, note } = SCORERS[ax.key](signals);
    return {
      key: ax.key, label: ax.label, weight: ax.weight,
      score, band: bandOf(score), note, fix: ax.fix,
    };
  });

  const observed = subscores.filter((s) => s.score !== null);
  let total = null;
  if (observed.length) {
    const wsum = observed.reduce((a, s) => a + s.weight, 0);
    const acc = observed.reduce((a, s) => a + s.score * s.weight, 0);
    total = wsum > 0 ? clamp(r0(acc / wsum), 0, 100) : null;
  }

  // Weakest links: observed axes scoring below "fair", most-fragile first.
  const weakest_links = observed
    .filter((s) => s.score < 60)
    .sort((a, b) => a.score - b.score)
    .map((s) => ({ key: s.key, label: s.label, score: s.score, note: s.note, fix: s.fix }));

  return {
    read_only: true,
    verdict: 'heuristic',
    score: total,
    band: bandOf(total),
    observed: observed.length,
    total_signals: AXES.length,
    subscores,
    weakest_links,
    caveat: CAVEAT,
  };
}

module.exports = {
  scoreReadiness,
  // exported for tests / reuse
  scoreEnvelope, scoreRecorder, scoreDrawdown,
  scoreConcentration, scoreCounterparty, scoreLivegate,
  bandOf, AXES, DRAWDOWN_REF_PCT, CAVEAT,
};
