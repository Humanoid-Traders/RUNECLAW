'use strict';
/**
 * Systemic Risk Sentinel — a market-wide read of CROWDING and herding across the
 * USDT-perp universe, computed from PUBLIC market data (funding, open interest,
 * ΔOI, 24h direction). When positioning piles onto one side, funding runs hot,
 * leverage surges and moves correlate, a coordinated unwind / liquidation
 * cascade gets more likely — this surfaces that as heuristic FLAGS, never a
 * verdict or a signal to trade.
 *
 * §4: public market facts only (OI/funding/breadth). Market OI in dollars is a
 * public fact, not a user's P&L. Flags are heuristic reads, explicitly not
 * advice. Pure + deterministic so the route can cache and tests can assert it.
 */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const num = (v, d = 0) => { const n = Number(v); return Number.isFinite(n) ? n : d; };

// Thresholds (documented so the flags are legible, not magic).
const FUND_HOT_BPS = 5;     // |funding| ≥ 5 bps/interval → a crowded book
const DOI_SURGE_PCT = 12;   // ΔOI ≥ +12% since the last poll → leverage piling in
const HERD_FLOOR = 0.55;    // same-direction share where herding starts to count

function level(score) {
  return score >= 75 ? 'high' : score >= 50 ? 'elevated' : score >= 25 ? 'building' : 'calm';
}

/**
 * @param coins strength-map coins: { base, symbol, funding(fraction), oi_usd,
 *              doi_pct, dir(-1..1), change_pct, long_score, short_score, ... }
 * @returns systemic read (see fields below)
 */
function buildSentinel(coins, now) {
  const list = (coins || []).filter((c) => c && num(c.oi_usd) > 0);
  const n = list.length;
  const empty = {
    generated_at: new Date(num(now, Date.now())).toISOString(),
    universe: 0, total_oi_usd: 0,
    gauge: { score: 0, level: 'calm' },
    bias: { long_share_pct: 50, label: 'balanced' },
    funding: { avg_bps: 0, crowded_long: [], crowded_short: [] },
    leverage: { surging: [] },
    herding: { same_dir_pct: 50, direction: 'flat' },
    flags: [], note: 'No market data right now.',
  };
  if (!n) return empty;

  const totalOi = list.reduce((a, c) => a + num(c.oi_usd), 0);
  const w = (c) => (totalOi > 0 ? num(c.oi_usd) / totalOi : 1 / n);

  // OI-weighted funding (bps) and OI-weighted long lean.
  let avgFunding = 0, longOi = 0;
  let up = 0, down = 0;
  for (const c of list) {
    avgFunding += num(c.funding) * w(c);
    // Smooth long lean per coin: dir −1..1 → 0..1 (neutral dir=0 reads 50/50).
    longOi += num(c.oi_usd) * ((clamp(num(c.dir), -1, 1) + 1) / 2);
    if (num(c.change_pct) > 0) up++; else if (num(c.change_pct) < 0) down++;
  }
  const avgFundingBps = avgFunding * 10000;
  const longSharePct = totalOi > 0 ? (longOi / totalOi) * 100 : 50;

  // Crowded books by funding extreme (biggest OI first).
  const byOi = (a, b) => num(b.oi_usd) - num(a.oi_usd);
  const slim = (c) => ({
    base: c.base || String(c.symbol || '').replace(/USDT$/, ''),
    funding_bps: Math.round(num(c.funding) * 10000 * 100) / 100,
    doi_pct: Math.round(num(c.doi_pct) * 100) / 100,
    oi_usd: Math.round(num(c.oi_usd)),
    change_pct: Math.round(num(c.change_pct) * 100) / 100,
  });
  const crowdedLong = list.filter((c) => num(c.funding) * 10000 >= FUND_HOT_BPS).sort(byOi).slice(0, 8).map(slim);
  const crowdedShort = list.filter((c) => num(c.funding) * 10000 <= -FUND_HOT_BPS).sort(byOi).slice(0, 8).map(slim);
  const surging = list.filter((c) => num(c.doi_pct) >= DOI_SURGE_PCT)
    .sort((a, b) => num(b.doi_pct) - num(a.doi_pct)).slice(0, 8).map(slim);

  const sameDirShare = Math.max(up, down) / n;      // 0.5..1
  const herdDir = up >= down ? 'up' : 'down';

  // Component scores 0..1.
  const fundingCrowd = clamp(Math.abs(avgFundingBps) / FUND_HOT_BPS, 0, 1);
  const herding = clamp((sameDirShare - HERD_FLOOR) / (1 - HERD_FLOOR), 0, 1);
  const leverage = clamp((surging.length / n) / 0.18, 0, 1);
  const sideLean = clamp(Math.abs(longSharePct - 50) / 30, 0, 1);   // >80/20 → max
  const score = Math.round(100 * (0.32 * herding + 0.28 * fundingCrowd + 0.22 * leverage + 0.18 * sideLean));

  // Heuristic flags — reads, not verdicts.
  const flags = [];
  if (Math.abs(avgFundingBps) >= FUND_HOT_BPS) {
    const longCrowd = avgFundingBps > 0;
    flags.push({ kind: 'funding', severity: fundingCrowd >= 0.8 ? 'high' : 'medium',
      text: `Funding is ${longCrowd ? 'positive' : 'negative'} market-wide (${avgFundingBps.toFixed(1)} bps) — `
        + `${longCrowd ? 'longs are paying to hold, a crowded-long tell (squeeze-DOWN risk)' : 'shorts are paying, a crowded-short tell (squeeze-UP risk)'}.` });
  }
  if (herding >= 0.4) {
    flags.push({ kind: 'herding', severity: herding >= 0.75 ? 'high' : 'medium',
      text: `${Math.round(sameDirShare * 100)}% of the universe is moving ${herdDir} together — correlated, low-dispersion tape where a single shock hits everything at once.` });
  }
  if (surging.length >= Math.max(3, n * 0.08)) {
    flags.push({ kind: 'leverage', severity: leverage >= 0.7 ? 'high' : 'medium',
      text: `${surging.length} coins are seeing open-interest surge ≥${DOI_SURGE_PCT}% — fresh leverage piling in, which fuels a faster unwind if it turns.` });
  }
  if (sideLean >= 0.5) {
    flags.push({ kind: 'bias', severity: sideLean >= 0.8 ? 'high' : 'medium',
      text: `${longSharePct.toFixed(0)}% of open interest leans ${longSharePct >= 50 ? 'long' : 'short'} — one-sided positioning concentrates liquidation levels.` });
  }
  if (!flags.length) flags.push({ kind: 'calm', severity: 'low', text: 'No systemic crowding stands out — positioning is broad and funding is near neutral.' });

  return {
    generated_at: new Date(num(now, Date.now())).toISOString(),
    universe: n, total_oi_usd: Math.round(totalOi),
    gauge: { score, level: level(score) },
    bias: { long_share_pct: Math.round(longSharePct * 10) / 10,
      label: sideLean >= 0.5 ? (longSharePct >= 50 ? 'crowded long' : 'crowded short') : 'balanced' },
    funding: { avg_bps: Math.round(avgFundingBps * 100) / 100, crowded_long: crowdedLong, crowded_short: crowdedShort },
    leverage: { surging },
    herding: { same_dir_pct: Math.round(sameDirShare * 1000) / 10, direction: herdDir },
    flags,
    disclaimer: 'Public market data · heuristic crowding read, not a verdict and not investment advice.',
  };
}

module.exports = { buildSentinel, FUND_HOT_BPS, DOI_SURGE_PCT };
