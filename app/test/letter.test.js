'use strict';
/**
 * The Agent Letter: ISO-week math, deterministic composition from recorded
 * data (honest empty states, losing weeks read like losing weeks), lazy
 * once-per-week generation with a single push announcement, the REST
 * surface, and the chat intercept.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
delete process.env.WEB_GATEWAY_SECRET;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const { pool } = require('../db');
const letter = require('../lib/letter');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/letter', require('../routes/letter'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function req(method, path, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

let userSeq = 0;
async function newUser() {
  userSeq++;
  const r = await req('POST', '/api/auth/register', {
    body: { email: `letter${userSeq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

// ── ISO week math ────────────────────────────────────────────────────────────

test('weekKey: ISO-8601 in UTC, including year boundaries', () => {
  assert.equal(letter.weekKey(new Date('2026-07-16T12:00:00Z')), '2026-W29');
  assert.equal(letter.weekKey(new Date('2026-01-01T00:00:00Z')), '2026-W01');
  // 2027-01-01 is a Friday → still ISO week 53 of 2026.
  assert.equal(letter.weekKey(new Date('2027-01-01T00:00:00Z')), '2026-W53');
});

test('lastCompletedWeek: Mon..Mon UTC window ending before today', () => {
  // Thu 2026-07-16 → last completed week is Mon 07-06 .. Mon 07-13 (excl).
  const w = letter.lastCompletedWeek(new Date('2026-07-16T09:00:00Z'));
  assert.equal(w.start.toISOString().slice(0, 10), '2026-07-06');
  assert.equal(w.end.toISOString().slice(0, 10), '2026-07-13');
  assert.equal(w.key, '2026-W28');
  // A Monday: the week that JUST ended is last week, not the one starting today.
  const mon = letter.lastCompletedWeek(new Date('2026-07-13T00:30:00Z'));
  assert.equal(mon.end.toISOString().slice(0, 10), '2026-07-13');
});

// ── Composition ──────────────────────────────────────────────────────────────

const WEEK = {
  key: '2026-W28',
  start: new Date('2026-07-06T00:00:00Z'),
  end: new Date('2026-07-13T00:00:00Z'),
};

test('composeLetter: a winning week reads like one, every figure real', () => {
  const L = letter.composeLetter(WEEK, {
    trades: [
      { symbol: 'BTC/USDT', pnl: 120, fees: 2, size_usd: 1000, closed_at: '2026-07-07T10:00:00Z' },
      { symbol: 'SOL/USDT', pnl: -40, fees: 2, size_usd: 800, closed_at: '2026-07-08T10:00:00Z' },
      { symbol: 'ETH/USDT', pnl: 60, fees: 2, size_usd: 900, closed_at: '2026-07-09T10:00:00Z' },
    ],
    equity: { start: 10000, end: 10140 },
    signals: [
      { symbol: 'BTC/USDT', direction: 'LONG', regime: 'TREND_UP', created_at: '2026-07-07T09:00:00Z' },
      { symbol: 'SOL/USDT', direction: 'SHORT', regime: 'TREND_UP', created_at: '2026-07-08T09:00:00Z' },
    ],
    openCount: 2,
    reports: { arb: { total_accrued_usd: 12.5 }, parity: { verdict: 'ALIGNED' } },
  });
  assert.equal(L.week_key, '2026-W28');
  assert.equal(L.period.start, '2026-07-06');
  assert.equal(L.period.end, '2026-07-12');           // inclusive end day
  assert.match(L.headline, /\$140 net — 67% winners/);
  const all = L.sections.map(s => `${s.title}: ${s.html}`).join('\n');
  assert.match(all, /A grinder's week|A clean week/);
  assert.match(all, /\$140/);                          // net
  assert.match(all, /best: BTC \$120/);
  assert.match(all, /worst: SOL -\$40/);
  assert.match(all, /\$10,000 → <b>\$10,140<\/b>/);
  assert.match(all, /2 signals generated \(1 long \/ 1 short\)/);
  assert.match(all, /TREND_UP/);
  assert.match(all, /\$12\.5.*hypothetical carry/);
  assert.match(all, /ALIGNED/);
  assert.match(all, /<b>2<\/b> open positions/);
  assert.match(L.footer, /nothing hand-written/i);
});

test('composeLetter: a losing week is called a losing week', () => {
  const L = letter.composeLetter(WEEK, {
    trades: [
      { symbol: 'BTC/USDT', pnl: -100, size_usd: 1000, closed_at: '2026-07-07T10:00:00Z' },
      { symbol: 'SOL/USDT', pnl: 20, size_usd: 800, closed_at: '2026-07-08T10:00:00Z' },
    ],
    equity: { start: null, end: null }, signals: [], openCount: 0, reports: null,
  });
  assert.match(L.headline, /-\$80 net — the honest post-mortem/);
  const weekSec = L.sections.find(s => s.title === 'The week');
  assert.match(weekSec.html, /A losing week, plainly/);
  const tape = L.sections.find(s => s.title === 'The tape');
  assert.match(tape.html, /No signals recorded/);
});

test('composeLetter: an empty week says so — nothing invented', () => {
  const L = letter.composeLetter(WEEK, {
    trades: [], equity: { start: null, end: null }, signals: [], openCount: 0, reports: null,
  });
  assert.equal(L.headline, 'A flat week, by choice');
  const weekSec = L.sections.find(s => s.title === 'The week');
  assert.match(weekSec.html, /closed no positions/);
  assert.ok(!L.sections.some(s => s.title === 'Performance'));
  assert.ok(!L.sections.some(s => s.title === 'Equity'));
  const ahead = L.sections.find(s => s.title === 'Looking ahead');
  assert.match(ahead.html, /enters the week flat/);
});

// ── Lazy generation + announcement ───────────────────────────────────────────

test('getLetter: generates once, then reuses the stored letter', async () => {
  const w = { key: '2025-W10', start: new Date('2025-03-03T00:00:00Z'),
              end: new Date('2025-03-10T00:00:00Z') };
  const first = await letter.getLetter(w);
  assert.equal(first.created, true);
  const second = await letter.getLetter(w);
  assert.equal(second.created, false);
  assert.deepEqual(second.letter, first.letter);
});

test('sweepLetters: announces exactly once', async () => {
  const pushes = [];
  const notify = async (payload, userIds) => { pushes.push({ payload, userIds }); };
  const created = await letter.sweepLetters(notify);
  assert.equal(created, true);
  assert.equal(pushes.length, 1);
  assert.match(pushes[0].payload.title, /weekly agent letter/i);
  assert.equal(pushes[0].userIds, null);   // all subscribers

  const again = await letter.sweepLetters(notify);
  assert.equal(again, false);
  assert.equal(pushes.length, 1);          // no re-announcement
});

// ── REST + chat ──────────────────────────────────────────────────────────────

test('REST: latest + archive + specific week; anonymous rejected', async () => {
  const anon = await req('GET', '/api/letter/latest');
  assert.equal(anon.status, 401);

  const token = await newUser();
  const latest = await req('GET', '/api/letter/latest', { token });
  assert.equal(latest.status, 200);
  assert.ok(latest.data.letter.week_key);
  assert.ok(latest.data.letter.sections.length >= 3);

  const arc = await req('GET', '/api/letter/archive', { token });
  assert.equal(arc.status, 200);
  assert.ok(arc.data.letters.length >= 1);

  const byKey = await req('GET', `/api/letter/${latest.data.letter.week_key}`, { token });
  assert.equal(byKey.status, 200);
  assert.equal(byKey.data.letter.week_key, latest.data.letter.week_key);

  const missing = await req('GET', '/api/letter/1999-W01', { token });
  assert.equal(missing.status, 404);
});

test('chat: "this week\'s letter" returns the letter; other text proxies', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', {
    token, body: { text: "show me this week's letter" },
  });
  assert.equal(r.status, 200);
  assert.equal(r.data.intent, 'letter');
  assert.match(r.data.reply_html, /The Agent Letter — \d{4}-W\d{2}/);
  assert.match(r.data.reply_html, /Looking ahead/);

  const other = await req('POST', '/api/chat', { token, body: { text: 'read me a poem' } });
  assert.equal(other.status, 503);   // unconfigured bot proxy
});
