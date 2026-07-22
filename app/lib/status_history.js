'use strict';
/**
 * NB2 — 24h uptime history for the public /status trust page.
 *
 * A lightweight in-memory ring of overall-status samples, plus PURE helpers to
 * prune and fold them into fixed time buckets for a timeline. Samples are
 * recorded every time the status API is hit (the page polls it every 60s), so
 * the history fills in on its own. It is deliberately in-memory: it carries no
 * secrets and no account data, and "history since the web tier last started" is
 * an honest thing to show — a restart is itself signal.
 *
 * Worst-state-wins per bucket (degraded beats partial beats ok), so a timeline
 * cell never rounds a bad minute up to healthy.
 */

// Overall-status severity. Higher = worse; the worst sample colours a bucket.
const RANK = { ok: 0, partial: 1, degraded: 2 };
const WINDOW_MS = 24 * 60 * 60 * 1000;
const MAX_SAMPLES = 5000; // ~24h at one sample / 17s — a generous safety cap.

let _samples = []; // [{ t: epochMs, status: 'ok'|'partial'|'degraded' }]

function normStatus(s) {
  return RANK[s] === undefined ? 'partial' : s; // unknown → treat as partial
}

/** Drop samples older than `cutoffMs`. Pure. */
function pruneOlderThan(samples, cutoffMs) {
  return (samples || []).filter((s) => s && s.t >= cutoffMs);
}

/**
 * Fold samples into `bucketCount` contiguous buckets of `bucketMs`, ending at
 * `now`. Returns oldest→newest: [{ start, end, status }] where status is the
 * WORST overall status seen in the window, or 'no_data' when the window is
 * empty. Pure.
 */
function bucketize(samples, now, bucketCount, bucketMs) {
  const out = [];
  const startOfAll = now - bucketCount * bucketMs;
  for (let i = 0; i < bucketCount; i++) {
    const start = startOfAll + i * bucketMs;
    const end = start + bucketMs;
    let worstRank = -1;
    let worst = 'no_data';
    for (const s of samples || []) {
      if (!s || s.t < start || s.t >= end) continue;
      const r = RANK[normStatus(s.status)];
      if (r > worstRank) { worstRank = r; worst = normStatus(s.status); }
    }
    out.push({ start, end, status: worst });
  }
  return out;
}

/** Record one overall-status sample (mutates the in-memory ring). */
function record(status, at) {
  const t = Number(at);
  if (!isFinite(t)) return;
  _samples.push({ t, status: normStatus(status) });
  _samples = pruneOlderThan(_samples, t - WINDOW_MS);
  if (_samples.length > MAX_SAMPLES) _samples = _samples.slice(-MAX_SAMPLES);
}

/** Current retained samples (already ≤ 24h old). */
function samples() { return _samples.slice(); }

/** Uptime % over the window: fraction of non-empty buckets that were healthy. */
function uptimePct(buckets) {
  const seen = (buckets || []).filter((b) => b.status !== 'no_data');
  if (!seen.length) return null;
  const good = seen.filter((b) => b.status === 'ok').length;
  return Math.round((good / seen.length) * 1000) / 10; // one decimal
}

function _reset() { _samples = []; } // test hook

module.exports = {
  RANK, WINDOW_MS, pruneOlderThan, bucketize, record, samples, uptimePct, _reset,
};
