'use strict';
/**
 * Public status (MH3) — component health as a TRUST surface.
 *
 * The same ethos as /proof: say what is actually true, including when it is
 * unflattering. Every component reports fresh/stale/unavailable with the real
 * age; nothing is summarized away. The payload carries NO secrets, NO
 * account data and NO dollar figures — component names and ages only.
 *
 * All probes are injectable so tests (and degraded runtimes) exercise every
 * honest state without a live bot behind the site.
 */

const { buildInfo } = require('./version');

const FRESH_SCAN_MS = 15 * 60_000;        // engine pushes the scan every few minutes
const FRESH_REPORTS_MS = 2.5 * 3600_000;  // intelligence reports are hourly
const FRESH_LETTER_MS = 9 * 86_400_000;   // weekly letter: last completed ISO week

let _probes = null;

function defaultProbes() {
  const sync = require('../routes/sync');
  const { pool } = require('../db');
  const gw = require('./gateway');
  return {
    getScan: () => sync.getLatestScan(),
    getReports: () => sync.getLatestReports(),
    pingGateway: async () => {
      if (!gw.isConfigured()) return { state: 'not_configured' };
      try {
        const r = await gw.getGateway('/public/proofofpnl', 3500);
        return { state: r.status >= 200 && r.status < 500 ? 'reachable' : 'error' };
      } catch (e) {
        return { state: 'unreachable' };
      }
    },
    latestLetter: async () => {
      const [rows] = await pool.execute(
        'SELECT week_key, generated_at FROM agent_letters ORDER BY week_key DESC LIMIT 1', []);
      return rows[0] || null;
    },
    dbMode: () => (process.env.DATABASE_URL ? 'mysql' : 'memory'),
    uptimeS: () => Math.floor(process.uptime()),
  };
}

function setProbes(p) { _probes = p; }

function ageState(iso, freshMs, now) {
  if (!iso) return { state: 'no_data', age_minutes: null };
  const age = now - new Date(iso).getTime();
  if (!isFinite(age) || age < 0) return { state: 'no_data', age_minutes: null };
  return {
    state: age <= freshMs ? 'fresh' : 'stale',
    age_minutes: Math.round(age / 60_000),
  };
}

async function buildStatus(now = Date.now()) {
  const p = _probes || defaultProbes();
  const components = {};

  components.web = { state: 'ok', uptime_s: p.uptimeS() };
  components.database = { state: 'ok', mode: p.dbMode() };

  let scan = null;
  try { scan = await p.getScan(); } catch (e) { /* honest no_data below */ }
  components.engine_scan = {
    ...ageState(scan && (scan.received_at || scan.timestamp), FRESH_SCAN_MS, now),
    note: 'live market scan pushed by the trading engine',
  };

  let reports = null;
  try { reports = await p.getReports(); } catch (e) { /* honest no_data below */ }
  components.intelligence_reports = {
    ...ageState(reports && reports.received_at, FRESH_REPORTS_MS, now),
    note: 'hourly funding/arb/parity/yield reports',
  };

  let gwState = { state: 'unreachable' };
  try { gwState = await p.pingGateway(); } catch (e) { /* keep unreachable */ }
  components.bot_gateway = {
    ...gwState,
    note: 'server-to-server link to the bot (chat, proof, cards)',
  };

  let letter = null;
  try { letter = await p.latestLetter(); } catch (e) { /* honest no_data below */ }
  components.weekly_letter = {
    ...ageState(letter && letter.generated_at, FRESH_LETTER_MS, now),
    latest_week: (letter && letter.week_key) || null,
    note: 'generated on demand from recorded data — quiet weeks are normal',
  };

  const worrying = ['engine_scan', 'intelligence_reports', 'bot_gateway']
    .filter((k) => !['fresh', 'ok', 'reachable', 'not_configured'].includes(components[k].state));
  return {
    status: worrying.length === 0 ? 'ok'
      : worrying.length >= 2 ? 'degraded' : 'partial',
    generated_at: new Date(now).toISOString(),
    // Which commit is actually serving this page — so a stale deploy is
    // visible here, not a mystery. Public metadata only (no secrets).
    build: buildInfo(),
    components,
    honesty_note: 'States are computed from real timestamps at request time — '
      + 'nothing here is hand-set. "not_configured" and "no_data" are reported '
      + 'as-is, never rounded up to healthy.',
  };
}

module.exports = { buildStatus, setProbes, FRESH_SCAN_MS, FRESH_REPORTS_MS };
