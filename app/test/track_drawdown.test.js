'use strict';
/**
 * Track-record drawdown honesty: a capital event (paper $10k history followed
 * by a live account holding a few hundred dollars, a deposit, a withdrawal)
 * must never be reported as a trading drawdown or rendered as a cliff in the
 * public equity curve — the exact bug where the page showed "98.7% max
 * drawdown" against +$4 net PnL over 48 trades.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const { pool } = require('../db');
const track = require('../routes/track');

const { maxDrawdownPct, segmentByCapitalEvents, segmentedMaxDrawdownPct } = track;

const pt = (t, equity) => ({ t, equity });

test('maxDrawdownPct: plain peak-to-trough on a consistent series', () => {
  const curve = [pt(1, 100), pt(2, 120), pt(3, 90), pt(4, 110)];
  // Peak 120 -> trough 90 = 25%.
  assert.equal(maxDrawdownPct(curve), 25);
});

test('segmentByCapitalEvents: paper→live switch splits the series', () => {
  // Paper history around $10k, then the live account holding ~$574 — the
  // step is unexplained by any trade PnL and must start a new segment.
  const curve = [pt(1, 10000), pt(2, 10050), pt(3, 9980), pt(4, 574), pt(5, 578)];
  const segs = segmentByCapitalEvents(curve, []);
  assert.equal(segs.length, 2);
  assert.equal(segs[0].length, 3);
  assert.equal(segs[1][0].equity, 574);
});

test('segmentByCapitalEvents: a large step explained by trade PnL does NOT split', () => {
  // Equity halves, but a recorded closed trade lost that much in the window —
  // that IS a trading drawdown and must stay in one segment.
  const curve = [pt(1000, 100), pt(2000, 45)];
  const trades = [{ closed_at: new Date(1500).toISOString(), pnl: -55 }];
  const segs = segmentByCapitalEvents(curve, trades);
  assert.equal(segs.length, 1);
  assert.equal(segmentedMaxDrawdownPct(curve, trades), 55);
});

test('segmentByCapitalEvents: small swings never split (unrealised noise)', () => {
  const curve = [pt(1, 574), pt(2, 560), pt(3, 590), pt(4, 570)];
  assert.equal(segmentByCapitalEvents(curve, []).length, 1);
});

test('segmentedMaxDrawdownPct: measures within segments, never across the switch', () => {
  // Paper segment: 10000 -> 9800 = 2% dd. Live segment: 580 -> 551 = 5% dd.
  // Naive cross-series dd would be ~94.5% — the bug.
  const curve = [pt(1, 10000), pt(2, 9800), pt(3, 580), pt(4, 551), pt(5, 574)];
  const dd = segmentedMaxDrawdownPct(curve, []);
  assert.equal(dd, 5);
  assert.ok(maxDrawdownPct(curve) > 90, 'naive computation reproduces the bug');
});

// ── Endpoint: seeded operator history with a capital switch ──────────────────

let server, base;

test.before(async () => {
  const now = Date.now();
  const day = 86400000;
  // Operator (user 1) closed trades: tiny PnLs like production.
  for (let i = 0; i < 4; i++) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
        size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [1, 'BTC/USDT', 'LONG', 100, 101, 50, i === 2 ? -0.5 : 1.5, 0.05, null,
       new Date(now - (8 - i) * day), new Date(now - (7 - i) * day)]);
  }
  // Snapshots: paper $10k era, then the live ~$574 era.
  const snaps = [
    [now - 9 * day, 10000], [now - 8 * day, 10012], [now - 7 * day, 9990],
    [now - 3 * day, 574], [now - 2 * day, 571], [now - 1 * day, 578],
  ];
  for (const [t, eq] of snaps) {
    await pool.execute(
      'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
      [1, eq, new Date(t)]);
  }
  const app = express();
  app.use('/api/public', track);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

function get(path) {
  return new Promise((resolve, reject) => {
    http.get(`${base}${path}`, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: JSON.parse(d) }));
    }).on('error', reject);
  });
}

test('GET /track-record: drawdown ignores the capital switch; curve is the current era', async () => {
  const r = await get('/api/public/track-record');
  assert.equal(r.status, 200);
  const s = r.data.stats;
  // Real trading dd: paper era 10012->9990 (~0.22%), live era 574->571
  // (~0.52%) — nothing remotely near the bogus 94%+.
  assert.ok(s.max_drawdown_pct != null && s.max_drawdown_pct < 5,
    `expected honest dd, got ${s.max_drawdown_pct}%`);
  assert.equal(r.data.capital_events, 1);
  // The rendered curve is the CURRENT capital basis only — no cliff.
  const eq = r.data.equity_curve.map(p => p.equity);
  assert.equal(Math.min(...eq) > 500 && Math.max(...eq) < 600, true,
    'curve must not include the paper-era points');
  assert.equal(s.current_equity_usd, 578);
});
