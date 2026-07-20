/**
 * The Agent Letter — a weekly fund-style letter written from REAL data.
 *
 * Every number and every sentence is derived from what the bot actually
 * recorded during the ISO week: closed trades, equity snapshots, the signal
 * stream, the public agent feed, and the bot's intelligence reports. The
 * phrasing adapts to the numbers (a losing week reads like a losing week),
 * but nothing is invented — no data for a section means the section says so.
 *
 * Letters are generated once per completed ISO week (UTC, Mon..Sun),
 * stored in agent_letters, and announced with a web push. Deterministic:
 * regenerating the same week from the same data yields the same letter.
 */

const { pool } = require('../db');

const OPERATOR_USER_ID = parseInt(process.env.BOT_USER_ID) || 1;

function round2(v) { return Math.round(v * 100) / 100; }

function money(v) {
  const n = Number(v) || 0;
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { maximumFractionDigits: 2 });
}

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ── ISO week math (UTC) ──────────────────────────────────────────────────────

/** ISO-8601 week key ('2026-W29') for a Date, computed in UTC. */
function weekKey(date) {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const day = d.getUTCDay() || 7;               // Mon=1..Sun=7
  d.setUTCDate(d.getUTCDate() + 4 - day);       // nearest Thursday
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((d - yearStart) / 86_400_000 + 1) / 7);
  return `${d.getUTCFullYear()}-W${String(week).padStart(2, '0')}`;
}

/** The last COMPLETED ISO week relative to `now`: { key, start, end }. */
function lastCompletedWeek(now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const day = d.getUTCDay() || 7;
  // Monday of the CURRENT week, then step back one week.
  const currentMonday = new Date(d);
  currentMonday.setUTCDate(d.getUTCDate() - (day - 1));
  const start = new Date(currentMonday);
  start.setUTCDate(currentMonday.getUTCDate() - 7);
  const end = currentMonday;                     // exclusive
  return { key: weekKey(start), start, end };
}

// ── Data gathering (all fail-soft) ───────────────────────────────────────────

async function loadWeekData(start, end) {
  const inWindow = (ts) => {
    const t = new Date(ts).getTime();
    return t >= start.getTime() && t < end.getTime();
  };

  let trades = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, pnl, fees, size_usd, opened_at, closed_at
         FROM trades
        WHERE user_id = ? AND status = 'CLOSED' AND closed_at IS NOT NULL
        ORDER BY closed_at ASC`, [OPERATOR_USER_ID]);
    trades = rows.filter(t => inWindow(t.closed_at));
  } catch (e) { /* section reports no data */ }

  let equity = { start: null, end: null };
  try {
    const [snaps] = await pool.execute(
      'SELECT equity, snapshot_at FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at ASC',
      [OPERATOR_USER_ID]);
    for (const s of snaps) {
      const t = new Date(s.snapshot_at).getTime();
      const v = parseFloat(s.equity);
      if (!isFinite(v)) continue;
      if (t < end.getTime()) equity.end = v;         // last one before week end
      if (t < start.getTime()) equity.start = v;     // last one before week start
    }
    // A week with snapshots only inside it: use the first in-window as start.
    if (equity.start === null) {
      const first = snaps.find(s => inWindow(s.snapshot_at));
      if (first) equity.start = parseFloat(first.equity);
    }
  } catch (e) { /* no equity section */ }

  let signals = [];
  try {
    const [rows] = await pool.execute(
      `SELECT symbol, direction, confidence, regime, created_at
         FROM signals ORDER BY created_at DESC LIMIT 500`, []);
    signals = rows.filter(s => inWindow(s.created_at));
  } catch (e) { /* no signals section */ }

  let openCount = 0;
  try {
    const [r] = await pool.execute(
      "SELECT COUNT(*) AS open_count FROM trades WHERE user_id = ? AND status = 'OPEN'",
      [OPERATOR_USER_ID]);
    openCount = parseInt(r[0]?.open_count) || 0;
  } catch (e) { /* stays 0 */ }

  let reports = null;
  try {
    const [r] = await pool.execute('SELECT reports_json FROM reports_cache WHERE id = 1');
    if (r.length && r[0].reports_json) reports = JSON.parse(r[0].reports_json);
  } catch (e) { /* no reports section */ }

  return { trades, equity, signals, openCount, reports };
}

// ── Composition (pure, deterministic) ────────────────────────────────────────

function composeLetter({ key, start, end }, data) {
  const { trades, equity, signals, openCount, reports } = data;
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(end.getTime() - 86_400_000);

  const pnls = trades.map(t => parseFloat(t.pnl) || 0);
  const net = round2(pnls.reduce((a, b) => a + b, 0));
  const wins = pnls.filter(p => p > 0).length;
  const grossWin = pnls.filter(p => p > 0).reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(pnls.filter(p => p < 0).reduce((a, b) => a + b, 0));
  const wr = trades.length ? Math.round(wins / trades.length * 100) : null;
  const pf = grossLoss > 0 ? round2(grossWin / grossLoss) : null;

  let best = null, worst = null;
  for (const t of trades) {
    const p = parseFloat(t.pnl) || 0;
    if (!best || p > best.pnl) best = { symbol: t.symbol, pnl: p };
    if (!worst || p < worst.pnl) worst = { symbol: t.symbol, pnl: p };
  }

  const sections = [];

  // ── The week ──
  let deskLine;
  if (!trades.length) {
    deskLine = 'The desk closed no positions this week — patience is a position too, '
      + 'and the risk gate saw nothing worth paying fees for.';
  } else if (net > 0 && (wr ?? 0) >= 60) {
    deskLine = `A clean week: ${trades.length} closed trades, ${wr}% winners, `
      + `<b>${money(net)}</b> net after fees.`;
  } else if (net > 0) {
    deskLine = `A grinder's week: ${trades.length} closed trades and only ${wr}% winners, `
      + `but the winners paid for the losers — <b>${money(net)}</b> net.`;
  } else {
    deskLine = `A losing week, plainly: ${trades.length} closed trades, ${wr}% winners, `
      + `<b>${money(net)}</b> net. The numbers below are the honest post-mortem.`;
  }
  sections.push({ title: 'The week', html: deskLine });

  // ── Performance ──
  if (trades.length) {
    const bits = [
      `Net PnL <b>${money(net)}</b> across ${trades.length} closes (${wins}W/${trades.length - wins}L)`,
      pf !== null ? `profit factor ${pf}` : null,
      best ? `best: ${esc(String(best.symbol).split('/')[0])} ${money(best.pnl)}` : null,
      worst && worst.pnl < 0 ? `worst: ${esc(String(worst.symbol).split('/')[0])} ${money(worst.pnl)}` : null,
    ].filter(Boolean).join(' · ');
    sections.push({ title: 'Performance', html: bits });
  }
  if (equity.start !== null && equity.end !== null && equity.start > 0) {
    const delta = round2(equity.end - equity.start);
    const pct = round2(delta / equity.start * 100);
    sections.push({
      title: 'Equity',
      html: `${money(equity.start)} → <b>${money(equity.end)}</b> `
        + `(${delta >= 0 ? '+' : ''}${money(delta).replace('$', '$')} · ${pct >= 0 ? '+' : ''}${pct}%).`,
    });
  }

  // ── The tape ──
  if (signals.length) {
    const longs = signals.filter(s => String(s.direction).toUpperCase().includes('LONG')).length;
    const regimes = {};
    for (const s of signals) {
      const r = String(s.regime || '').trim();
      if (r) regimes[r] = (regimes[r] || 0) + 1;
    }
    const topRegime = Object.entries(regimes).sort((a, b) => b[1] - a[1])[0];
    sections.push({
      title: 'The tape',
      html: `${signals.length} signals generated (${longs} long / ${signals.length - longs} short)`
        + (topRegime ? ` — the dominant read was <b>${esc(topRegime[0])}</b> `
          + `(${topRegime[1]} of ${signals.length}).` : '.'),
    });
  } else {
    sections.push({
      title: 'The tape',
      html: 'No signals recorded this week — either a quiet tape or the engine was resting.',
    });
  }

  // ── Side desks (bot intelligence reports, if the bot pushed them) ──
  if (reports) {
    const bits = [];
    try {
      const arb = reports.arb || {};
      if (arb.total_accrued_usd != null) {
        bits.push(`the funding-arb PAPER tracker has accrued ${money(arb.total_accrued_usd)} of hypothetical carry`);
      }
      const parity = reports.parity || {};
      if (parity.verdict) bits.push(`live↔backtest parity reads <b>${esc(parity.verdict)}</b>`);
    } catch (e) { /* skip */ }
    if (bits.length) {
      sections.push({ title: 'Side desks', html: bits.join('; ') + '.' });
    }
  }

  // ── Looking ahead ──
  sections.push({
    title: 'Looking ahead',
    html: (openCount
      ? `The desk carries <b>${openCount}</b> open position${openCount === 1 ? '' : 's'} into the new week, each with a hard stop working. `
      : 'The desk enters the week flat. ')
      + 'Same discipline as always: no trade without a stop, no size without conviction, '
      + 'and the risk gate has the final word.',
  });

  const headline = !trades.length
    ? 'A flat week, by choice'
    : net >= 0 ? `${money(net)} net — ${wr}% winners` : `${money(net)} net — the honest post-mortem`;

  return {
    week_key: key,
    period: { start: fmtDay(start), end: fmtDay(endInclusive) },
    headline,
    sections,
    footer: 'Every figure above is derived from recorded trades and snapshots — nothing hand-written. '
      + 'Past performance does not predict future results.',
  };
}

// ── Public variant (dollar-free) ─────────────────────────────────────────────

/** Inverse of weekKey: '2026-W29' -> { key, start (Mon, UTC), end (next Mon,
 *  exclusive) } or null when malformed. */
function weekRangeFromKey(key) {
  const m = /^(\d{4})-W(\d{2})$/.exec(String(key || ''));
  if (!m) return null;
  const year = parseInt(m[1]), week = parseInt(m[2]);
  if (week < 1 || week > 53) return null;
  // ISO: week 1 contains Jan 4. Monday of week 1, then step forward.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const day = jan4.getUTCDay() || 7;
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - (day - 1));
  const start = new Date(week1Monday);
  start.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7);
  if (weekKey(start) !== `${m[1]}-W${m[2]}`) return null;   // e.g. W53 in a 52-week year
  const end = new Date(start);
  end.setUTCDate(start.getUTCDate() + 7);
  return { key: String(key), start, end };
}

/**
 * The PUBLIC letter: same recorded data, recomposed with NO dollar figure —
 * counts, win rate, profit factor, equity PERCENT change, regime reads. Never
 * derived by stripping the private letter's HTML (too fragile to trust with a
 * privacy line); this is a parallel composition from the same loadWeekData.
 */
function composePublicLetter({ key, start, end }, data) {
  const { trades, equity, signals, openCount, reports } = data;
  const fmtDay = (d) => d.toISOString().slice(0, 10);
  const endInclusive = new Date(end.getTime() - 86_400_000);

  const pnls = trades.map(t => parseFloat(t.pnl) || 0);
  const net = pnls.reduce((a, b) => a + b, 0);
  const wins = pnls.filter(p => p > 0).length;
  const grossWin = pnls.filter(p => p > 0).reduce((a, b) => a + b, 0);
  const grossLoss = Math.abs(pnls.filter(p => p < 0).reduce((a, b) => a + b, 0));
  const wr = trades.length ? Math.round(wins / trades.length * 100) : null;
  const pf = grossLoss > 0 ? round2(grossWin / grossLoss) : null;

  let best = null, worst = null;
  for (const t of trades) {
    const p = parseFloat(t.pnl) || 0;
    if (!best || p > best.pnl) best = { symbol: t.symbol, pnl: p };
    if (!worst || p < worst.pnl) worst = { symbol: t.symbol, pnl: p };
  }

  const sections = [];

  let deskLine;
  if (!trades.length) {
    deskLine = 'The desk closed no positions this week — patience is a position too, '
      + 'and the risk gate saw nothing worth paying fees for.';
  } else if (net > 0 && (wr ?? 0) >= 60) {
    deskLine = `A clean week: ${trades.length} closed trades, <b>${wr}% winners</b>, `
      + 'finished green.';
  } else if (net > 0) {
    deskLine = `A grinder's week: ${trades.length} closed trades and only ${wr}% winners, `
      + 'but the winners paid for the losers — finished green.';
  } else {
    deskLine = `A losing week, plainly: ${trades.length} closed trades, ${wr}% winners, `
      + 'finished red. The reads below are the honest post-mortem.';
  }
  sections.push({ title: 'The week', html: deskLine });

  if (trades.length) {
    const bits = [
      `${trades.length} closes (${wins}W/${trades.length - wins}L)`,
      pf !== null ? `profit factor <b>${pf}</b>` : 'no losing trades',
      best ? `best: ${esc(String(best.symbol).split('/')[0])}` : null,
      worst && worst.pnl < 0 ? `worst: ${esc(String(worst.symbol).split('/')[0])}` : null,
    ].filter(Boolean).join(' · ');
    sections.push({ title: 'Performance', html: bits });
  }
  if (equity.start !== null && equity.end !== null && equity.start > 0) {
    const pct = round2((equity.end - equity.start) / equity.start * 100);
    sections.push({
      title: 'Equity',
      html: `Equity moved <b>${pct >= 0 ? '+' : ''}${pct}%</b> on the week.`,
    });
  }

  if (signals.length) {
    const longs = signals.filter(s => String(s.direction).toUpperCase().includes('LONG')).length;
    const regimes = {};
    for (const s of signals) {
      const r = String(s.regime || '').trim();
      if (r) regimes[r] = (regimes[r] || 0) + 1;
    }
    const topRegime = Object.entries(regimes).sort((a, b) => b[1] - a[1])[0];
    sections.push({
      title: 'The tape',
      html: `${signals.length} signals generated (${longs} long / ${signals.length - longs} short)`
        + (topRegime ? ` — the dominant read was <b>${esc(topRegime[0])}</b> `
          + `(${topRegime[1]} of ${signals.length}).` : '.'),
    });
  } else {
    sections.push({
      title: 'The tape',
      html: 'No signals recorded this week — either a quiet tape or the engine was resting.',
    });
  }

  // Side desks: parity verdict only — the funding-arb tracker's dollar accrual
  // stays operator-private.
  if (reports && reports.parity && reports.parity.verdict) {
    sections.push({
      title: 'Side desks',
      html: `Live↔backtest parity reads <b>${esc(reports.parity.verdict)}</b>.`,
    });
  }

  sections.push({
    title: 'Looking ahead',
    html: (openCount
      ? `The desk carries <b>${openCount}</b> open position${openCount === 1 ? '' : 's'} into the new week, each with a hard stop working. `
      : 'The desk enters the week flat. ')
      + 'Same discipline as always: no trade without a stop, no size without conviction, '
      + 'and the risk gate has the final word.',
  });

  const headline = !trades.length
    ? 'A flat week, by choice'
    : `${wr}% winners over ${trades.length} trades — `
      + (net >= 0 ? 'a green week' : 'a red week, honestly told');

  return {
    week_key: key,
    period: { start: fmtDay(start), end: fmtDay(endInclusive) },
    headline,
    sections,
    footer: 'Every figure above is derived from recorded trades and snapshots — nothing '
      + 'hand-written. Percentages and counts only: account size is never published. '
      + 'Past performance does not predict future results.',
  };
}

// The public letter for a COMPLETED week. Recomposed on demand from the same
// recorded data (deterministic for past weeks — the tables are append-only),
// cached in memory because a completed week is immutable. Never writes the DB.
const _publicCache = new Map();          // week_key -> public letter
const _PUBLIC_CACHE_MAX = 64;

async function getPublicLetter(key) {
  const week = weekRangeFromKey(key);
  if (!week) return null;
  // Only completed weeks: the in-progress week's letter doesn't exist yet.
  if (week.end.getTime() > lastCompletedWeek().end.getTime()) return null;
  if (_publicCache.has(week.key)) return _publicCache.get(week.key);
  const pub = composePublicLetter(week, await loadWeekData(week.start, week.end));
  if (_publicCache.size >= _PUBLIC_CACHE_MAX) {
    _publicCache.delete(_publicCache.keys().next().value);
  }
  _publicCache.set(week.key, pub);
  return pub;
}

// ── Storage + lazy generation ────────────────────────────────────────────────

async function getLetter(week) {
  const [rows] = await pool.execute(
    'SELECT week_key, generated_at, letter_json FROM agent_letters WHERE week_key = ?',
    [week.key]);
  if (rows.length) {
    return { generated_at: rows[0].generated_at, created: false,
             letter: JSON.parse(rows[0].letter_json) };
  }
  const letter = composeLetter(week, await loadWeekData(week.start, week.end));
  await pool.execute(
    'INSERT INTO agent_letters (week_key, generated_at, letter_json) VALUES (?, ?, ?)',
    [week.key, new Date(), JSON.stringify(letter)]);
  return { generated_at: new Date().toISOString(), created: true, letter };
}

async function listLetters(limit = 12) {
  const [rows] = await pool.execute(
    `SELECT week_key, generated_at FROM agent_letters ORDER BY week_key DESC LIMIT ${Math.min(limit, 52)}`,
    []);
  return rows;
}

async function getLetterByKey(key) {
  if (!/^\d{4}-W\d{2}$/.test(String(key))) return null;
  const [rows] = await pool.execute(
    'SELECT week_key, generated_at, letter_json FROM agent_letters WHERE week_key = ?', [key]);
  if (!rows.length) return null;
  return { generated_at: rows[0].generated_at, letter: JSON.parse(rows[0].letter_json) };
}

/**
 * Ensure the last completed week's letter exists; when this sweep is the one
 * that creates it, announce it with a push to every subscriber. Never throws.
 */
async function sweepLetters(notify) {
  try {
    const week = lastCompletedWeek();
    const r = await getLetter(week);
    if (r.created) {
      let send = notify;
      if (!send) {
        const { notifySubscribers } = require('./push');
        send = notifySubscribers;
      }
      try {
        await send({
          title: '📜 Your weekly agent letter is ready',
          body: `${week.key}: ${r.letter.headline}`,
          url: '/dashboard#home',
        }, null);
      } catch (e) { /* push best-effort */ }
      return true;
    }
    return false;
  } catch (e) {
    return false;
  }
}

let sweepTimer = null;
function startLetterSweep(intervalMs = 3_600_000) {
  if (sweepTimer) return;
  sweepLetters().catch(() => {});
  sweepTimer = setInterval(() => { sweepLetters().catch(() => {}); }, intervalMs);
  if (sweepTimer.unref) sweepTimer.unref();
}

// ── Chat intercept ───────────────────────────────────────────────────────────

const CHAT_RE = /\b(?:(?:this |last )?week'?s letter|weekly (?:agent )?letter|agent letter)\b/i;

async function maybeHandleLetterChat(userId, text) {
  if (!CHAT_RE.test(String(text || ''))) return null;
  try {
    const { letter } = await getLetter(lastCompletedWeek());
    const secs = letter.sections.map(s => `<b>${esc(s.title)}</b><br>${s.html}`).join('<br><br>');
    return {
      reply_html: `📜 <b>The Agent Letter — ${esc(letter.week_key)}</b> `
        + `<i>(${esc(letter.period.start)} → ${esc(letter.period.end)})</i><br><br>${secs}`
        + `<br><br><i>${esc(letter.footer)}</i>`,
      intent: 'letter',
    };
  } catch (e) {
    return { reply_html: 'The letter press jammed — try again in a moment.', intent: 'letter' };
  }
}

module.exports = {
  weekKey,
  weekRangeFromKey,
  lastCompletedWeek,
  composeLetter,
  composePublicLetter,
  getPublicLetter,
  getLetter,
  getLetterByKey,
  listLetters,
  sweepLetters,
  startLetterSweep,
  maybeHandleLetterChat,
};
