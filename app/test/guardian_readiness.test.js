'use strict';
/**
 * Guardian Readiness Score — the composed read-only agent-safety posture.
 *
 * Core is the pure scorer (lib/guardian_readiness.js): six axes → weighted
 * 0–100 total + weakest link. Locks the contract that matters for a SAFETY
 * surface: score bounds, "not yet observed" for missing signals (never a
 * silent pass), all-null → unknown (never a spurious number), a mandatory
 * heuristic caveat, and NO dollar amounts anywhere (§4). Plus a route smoke
 * test that the endpoint always returns a valid score object.
 */

const test = require('node:test');
const assert = require('node:assert');

const {
  scoreReadiness, bandOf, AXES, CAVEAT,
} = require('../lib/guardian_readiness');

const ALL = {
  envelope: { mode: 'enforce', bound: true },
  recorderOk: true,
  drawdownPct: 0,
  concentrationPct: 0.2,
  counterpartyTier: 'none',
  liveState: { live_enabled: false, allowlisted: false, paused: false },
};

test('a fully-safe posture scores high and reports all axes observed', () => {
  const out = scoreReadiness(ALL);
  assert.strictEqual(out.observed, AXES.length);
  assert.ok(out.score >= 90, `expected strong score, got ${out.score}`);
  assert.strictEqual(out.band, 'strong');
  assert.strictEqual(out.verdict, 'heuristic');
  assert.strictEqual(out.weakest_links.length, 0);
});

test('every sub-score stays within 0–100 and the total is bounded', () => {
  const out = scoreReadiness(ALL);
  for (const s of out.subscores) {
    if (s.score !== null) assert.ok(s.score >= 0 && s.score <= 100, `${s.key}=${s.score}`);
  }
  assert.ok(out.score >= 0 && out.score <= 100);
});

test('a missing signal is "not yet observed", never a silent pass', () => {
  const out = scoreReadiness({ ...ALL, recorderOk: null, counterpartyTier: null });
  const rec = out.subscores.find((s) => s.key === 'recorder');
  assert.strictEqual(rec.score, null);
  assert.strictEqual(rec.band, 'unknown');
  assert.match(rec.note, /not yet|no recorded|observed/i);
  // Excluded from the total (observed count drops), not counted as 0 or 100.
  assert.strictEqual(out.observed, AXES.length - 2);
});

test('nothing observed → total null and band unknown (no spurious number)', () => {
  const out = scoreReadiness({});
  assert.strictEqual(out.score, null);
  assert.strictEqual(out.band, 'unknown');
  assert.strictEqual(out.observed, 0);
  assert.strictEqual(out.weakest_links.length, 0);
});

test('an unconstrained agent surfaces the weak axes as weakest links, worst first', () => {
  const out = scoreReadiness({
    envelope: { mode: 'off', bound: false },   // 15
    recorderOk: false,                          // 0
    drawdownPct: 30,                            // 0 (over the band)
    concentrationPct: 0.9,                      // ~0
    counterpartyTier: 'high',                   // 20
    liveState: { live_enabled: true, allowlisted: false, paused: false }, // 30
  });
  assert.ok(out.score !== null && out.score < 40, `expected weak score, got ${out.score}`);
  assert.strictEqual(out.band, 'weak');
  assert.ok(out.weakest_links.length >= 3);
  // Sorted ascending by score.
  for (let i = 1; i < out.weakest_links.length; i++) {
    assert.ok(out.weakest_links[i - 1].score <= out.weakest_links[i].score);
  }
  // Each weak link carries a deep-link fix.
  for (const l of out.weakest_links) {
    assert.ok(l.fix && typeof l.fix.href === 'string' && l.fix.href.startsWith('#'));
  }
});

test('the heuristic caveat is always present (safety reads are flags, never verdicts)', () => {
  assert.strictEqual(scoreReadiness(ALL).caveat, CAVEAT);
  assert.strictEqual(scoreReadiness({}).caveat, CAVEAT);
  assert.match(CAVEAT, /heuristic|guarantee|not a promise/i);
});

test('NO dollar amounts appear anywhere in the score payload (§4)', () => {
  const json = JSON.stringify(scoreReadiness(ALL));
  assert.ok(!json.includes('$'), 'no dollar sign in the payload');
  assert.ok(!/usd/i.test(json), 'no usd field in the payload');
});

test('band thresholds: strong>=80, fair>=60, weak below, null→unknown', () => {
  assert.strictEqual(bandOf(80), 'strong');
  assert.strictEqual(bandOf(79), 'fair');
  assert.strictEqual(bandOf(60), 'fair');
  assert.strictEqual(bandOf(59), 'weak');
  assert.strictEqual(bandOf(null), 'unknown');
});

test('paper (not live) and de-risked both score the live-gate axis safe', () => {
  const paper = scoreReadiness({ ...ALL, liveState: { live_enabled: false, allowlisted: false, paused: false } });
  const paused = scoreReadiness({ ...ALL, liveState: { live_enabled: true, allowlisted: true, paused: true } });
  const lg = (o) => o.subscores.find((s) => s.key === 'livegate').score;
  assert.strictEqual(lg(paper), 100);
  assert.strictEqual(lg(paused), 100);
  // Live + operator-gated is exposed but constrained → mid, not full.
  const live = scoreReadiness({ ...ALL, liveState: { live_enabled: true, allowlisted: true, paused: false } });
  assert.ok(lg(live) < 100 && lg(live) >= 60);
});

// ── Route smoke: the endpoint always returns a valid score object ────────────
process.env.JWT_SECRET = process.env.JWT_SECRET || 'j'.repeat(64);

test('GET /api/guardian/readiness returns a heuristic score object for a logged-in user', async () => {
  const http = require('node:http');
  const express = require('express');
  const jwt = require('jsonwebtoken');
  const db = require('../db');

  await db.pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['readiness@test.io', 'x', 'Readiness']);
  const [rows] = await db.pool.execute('SELECT id, email FROM users WHERE email = ?',
    ['readiness@test.io']);
  const u = rows[0];
  const token = jwt.sign({ user_id: u.id, email: u.email }, process.env.JWT_SECRET);

  const app = express();
  app.use(express.json());
  app.use('/api/guardian/readiness', require('../routes/guardian_readiness'));
  const server = await new Promise((res) => { const s = app.listen(0, '127.0.0.1', () => res(s)); });
  const port = server.address().port;

  const body = await new Promise((resolve, reject) => {
    const req = http.request(`http://127.0.0.1:${port}/api/guardian/readiness`,
      { headers: { Authorization: `Bearer ${token}` } }, (r) => {
        let d = ''; r.on('data', (c) => d += c);
        r.on('end', () => resolve({ status: r.statusCode, data: JSON.parse(d || '{}') }));
      });
    req.on('error', reject); req.end();
  });
  server.close();

  assert.strictEqual(body.status, 200);
  assert.strictEqual(body.data.verdict, 'heuristic');
  assert.strictEqual(body.data.read_only, true);
  assert.ok(Array.isArray(body.data.subscores) && body.data.subscores.length === AXES.length);
  assert.ok('score' in body.data);                 // number or null, never absent
  assert.ok(typeof body.data.caveat === 'string' && body.data.caveat.length > 0);
  assert.ok(!JSON.stringify(body.data).includes('$'), 'no dollars in the live payload');
});
