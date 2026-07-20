'use strict';
/**
 * Public Agent Letter (community C3) — dollar-free permalinks.
 *
 * The privacy line under test: the PUBLIC letter is a parallel recomposition
 * from the same recorded data (counts, win rate, profit factor, equity PERCENT
 * change, regime reads) — never derived by stripping the private letter's
 * HTML, and never containing a dollar figure, so account size cannot leak
 * from a shared letter. Only completed weeks resolve; the private JWT surface
 * is untouched.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const letter = require('../lib/letter');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/public/letter', require('../routes/public_letter'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function get(path) {
  return new Promise((resolve, reject) => {
    const r = http.request(`${base}${path}`, { method: 'GET' }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    r.end();
  });
}

const WEEK = {
  key: '2026-W28',
  start: new Date('2026-07-06T00:00:00Z'),
  end: new Date('2026-07-13T00:00:00Z'),
};

const RICH_DATA = {
  trades: [
    { symbol: 'BTC/USDT', pnl: 120, closed_at: '2026-07-07T10:00:00Z' },
    { symbol: 'SOL/USDT', pnl: -40, closed_at: '2026-07-08T10:00:00Z' },
    { symbol: 'ETH/USDT', pnl: 60, closed_at: '2026-07-09T10:00:00Z' },
  ],
  equity: { start: 10000, end: 10140 },
  signals: [
    { symbol: 'BTC/USDT', direction: 'LONG', regime: 'TREND_UP', created_at: '2026-07-07T09:00:00Z' },
    { symbol: 'SOL/USDT', direction: 'SHORT', regime: 'TREND_UP', created_at: '2026-07-08T09:00:00Z' },
  ],
  openCount: 2,
  reports: { arb: { total_accrued_usd: 12.5 }, parity: { verdict: 'ALIGNED' } },
};

// ── weekRangeFromKey (inverse of weekKey) ────────────────────────────────────

test('weekRangeFromKey: round-trips with weekKey, rejects junk', () => {
  const w = letter.weekRangeFromKey('2026-W28');
  assert.equal(w.start.toISOString().slice(0, 10), '2026-07-06');
  assert.equal(w.end.toISOString().slice(0, 10), '2026-07-13');
  assert.equal(letter.weekKey(w.start), '2026-W28');
  // 2026 has 53 ISO weeks; 2025-W53 does not exist (52-week year).
  assert.ok(letter.weekRangeFromKey('2026-W53'));
  assert.equal(letter.weekRangeFromKey('2025-W53'), null);
  for (const bad of ['2026-W00', '2026-W54', 'W28', '2026-28', 'DROP TABLE', '']) {
    assert.equal(letter.weekRangeFromKey(bad), null, `rejects ${bad}`);
  }
});

// ── composePublicLetter: THE dollar-free contract ────────────────────────────

test('public letter NEVER contains a dollar figure — the whole point', () => {
  const pub = letter.composePublicLetter(WEEK, RICH_DATA);
  const s = JSON.stringify(pub);
  assert.ok(!/\$\s*[\d.]/.test(s), `no $amount anywhere: ${s.slice(0, 400)}`);
  assert.ok(!/10,?000|10,?140/.test(s), 'no raw equity values');
  assert.ok(!/\b120\b|\b-40\b/.test(s), 'no raw per-trade PnL values');
  // The compare point: the PRIVATE letter for the same data DOES carry dollars.
  const priv = letter.composeLetter(WEEK, RICH_DATA);
  assert.ok(/\$\d/.test(JSON.stringify(priv)), 'private letter still has dollars');
});

test('public letter keeps the size-agnostic substance', () => {
  const pub = letter.composePublicLetter(WEEK, RICH_DATA);
  const all = pub.sections.map(s => `${s.title}: ${s.html}`).join('\n');
  assert.match(pub.headline, /67% winners over 3 trades/);
  assert.match(all, /3 closes \(2W\/1L\)/);
  assert.match(all, /profit factor <b>4\.5<\/b>/);       // 180/40
  assert.match(all, /best: BTC/);
  assert.match(all, /worst: SOL/);
  assert.match(all, /\+1\.4%/);                           // equity percent only
  assert.match(all, /2 signals generated \(1 long \/ 1 short\)/);
  assert.match(all, /TREND_UP/);
  assert.match(all, /ALIGNED/);                           // parity verdict kept
  assert.ok(!/hypothetical carry/.test(all), 'arb dollar accrual stays private');
  assert.match(all, /<b>2<\/b> open positions/);
  assert.match(pub.footer, /account size is never published/i);
});

test('a losing week is still called a losing week — honesty survives redaction', () => {
  const pub = letter.composePublicLetter(WEEK, {
    trades: [
      { symbol: 'BTC/USDT', pnl: -100, closed_at: '2026-07-07T10:00:00Z' },
      { symbol: 'SOL/USDT', pnl: 20, closed_at: '2026-07-08T10:00:00Z' },
    ],
    equity: { start: null, end: null }, signals: [], openCount: 0, reports: null,
  });
  assert.match(pub.headline, /a red week, honestly told/);
  assert.match(pub.sections[0].html, /A losing week, plainly/);
  assert.ok(!/\$\s*[\d.]/.test(JSON.stringify(pub)));
});

// ── REST surface ─────────────────────────────────────────────────────────────

test('GET /:week serves a completed week; response carries no dollars', async () => {
  const r = await get('/api/public/letter/2026-W20');
  assert.equal(r.status, 200);
  assert.equal(r.data.letter.week_key, '2026-W20');
  assert.ok(!/\$\s*[\d.]/.test(JSON.stringify(r.data)));
});

test('GET /latest serves the last completed week', async () => {
  const r = await get('/api/public/letter/latest');
  assert.equal(r.status, 200);
  assert.equal(r.data.letter.week_key, letter.lastCompletedWeek().key);
});

test('invalid, future, and in-progress weeks 404', async () => {
  assert.equal((await get('/api/public/letter/not-a-week')).status, 404);
  assert.equal((await get('/api/public/letter/2099-W01')).status, 404);
  const current = letter.weekKey(new Date());          // in-progress week
  assert.equal((await get(`/api/public/letter/${current}`)).status, 404);
});

test('determinism: two fetches of the same completed week are identical', async () => {
  const a = await get('/api/public/letter/2026-W21');
  const b = await get('/api/public/letter/2026-W21');
  assert.deepEqual(a.data, b.data);
});

test('archive lists week keys only — no content, no dollars', async () => {
  // Seed a stored (private) letter so the archive has an entry.
  await letter.getLetter(letter.lastCompletedWeek());
  const r = await get('/api/public/letter/archive');
  assert.equal(r.status, 200);
  assert.ok(Array.isArray(r.data.weeks));
  for (const k of r.data.weeks) assert.match(k, /^\d{4}-W\d{2}$/);
});
