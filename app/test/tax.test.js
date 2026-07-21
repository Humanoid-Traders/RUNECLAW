/**
 * Continuous Tax & Compliance Agent.
 *
 * Part 1 — pure realized-gains math (app/lib/tax.js): each closed round-trip is
 * one disposal; short/long-term at 365 days; per-year aggregation; Form-8949
 * CSV; "not tax advice" disclaimer.
 * Part 2 — the JWT-authed /api/tax route against the MemoryDB fallback: auth
 * required, report shape, year filter, CSV content-type.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 't'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');

const tax = require('../lib/tax');

// ── Part 1: pure math ────────────────────────────────────────────────────

test('classifyDisposal: a short round-trip is classified short-term', () => {
  const d = tax.classifyDisposal({
    symbol: 'BTC/USDT:USDT', direction: 'LONG', size_usd: 200, pnl: 12.5, fees: 0.2,
    opened_at: '2025-01-01T00:00:00Z', closed_at: '2025-01-10T00:00:00Z',
  });
  assert.strictEqual(d.term, 'short');
  assert.strictEqual(d.holding_days, 9);
  assert.strictEqual(d.cost_basis, 200);
  assert.strictEqual(d.gain_loss, 12.5);
  assert.strictEqual(d.proceeds, 212.5); // identity: proceeds − basis = gain
  assert.strictEqual(d.fees, 0.2);
  assert.strictEqual(d.year, 2025);
});

test('classifyDisposal: a held-over-a-year round-trip is long-term', () => {
  const d = tax.classifyDisposal({
    symbol: 'ETH/USDT:USDT', direction: 'LONG', size_usd: 100, pnl: -50, fees: 0.1,
    opened_at: '2023-01-01T00:00:00Z', closed_at: '2024-06-01T00:00:00Z',
  });
  assert.strictEqual(d.term, 'long');
  assert.ok(d.holding_days >= 365);
  assert.strictEqual(d.gain_loss, -50);
  assert.strictEqual(d.proceeds, 50); // 100 basis − 50 loss
  assert.strictEqual(d.year, 2024);
});

test('classifyDisposal: an unclosed trade has no disposal date and unknown term', () => {
  const d = tax.classifyDisposal({ symbol: 'X', size_usd: 100, pnl: 5, opened_at: '2025-01-01T00:00:00Z', closed_at: null });
  assert.strictEqual(d.disposed, null);
  assert.strictEqual(d.term, 'unknown');
});

test('buildReport: aggregates per year with a short/long split and W/L counts', () => {
  const rows = [
    { symbol: 'A', direction: 'LONG', size_usd: 100, pnl: 10, fees: 0.5, opened_at: '2025-01-01T00:00:00Z', closed_at: '2025-01-05T00:00:00Z' },
    { symbol: 'B', direction: 'SHORT', size_usd: 100, pnl: -4, fees: 0.5, opened_at: '2025-02-01T00:00:00Z', closed_at: '2025-02-03T00:00:00Z' },
    { symbol: 'C', direction: 'LONG', size_usd: 100, pnl: 30, fees: 0.5, opened_at: '2023-01-01T00:00:00Z', closed_at: '2024-06-01T00:00:00Z' }, // long, 2024
    { symbol: 'D', direction: 'LONG', size_usd: 100, pnl: 1, fees: 0, opened_at: '2025-03-01T00:00:00Z', closed_at: null }, // unclosed → dropped
  ];
  const r = tax.buildReport(rows);
  assert.deepStrictEqual(r.available_years, [2025, 2024]);
  assert.strictEqual(r.totals.disposals, 3);
  assert.strictEqual(r.totals.gains, 2);
  assert.strictEqual(r.totals.losses, 1);
  assert.strictEqual(r.totals.net_gain_loss, 36); // 10 − 4 + 30
  assert.strictEqual(r.totals.short_term_gain_loss, 6); // 10 − 4
  assert.strictEqual(r.totals.long_term_gain_loss, 30);
  const y2025 = r.years.find(y => y.year === 2025);
  assert.strictEqual(y2025.disposals, 2);
  assert.strictEqual(y2025.net_gain_loss, 6);
});

test('buildReport: year filter restricts the disposals and scope', () => {
  const rows = [
    { symbol: 'A', size_usd: 100, pnl: 10, opened_at: '2025-01-01T00:00:00Z', closed_at: '2025-01-05T00:00:00Z' },
    { symbol: 'C', size_usd: 100, pnl: 30, opened_at: '2023-01-01T00:00:00Z', closed_at: '2024-06-01T00:00:00Z' },
  ];
  const r = tax.buildReport(rows, { year: 2024 });
  assert.strictEqual(r.scope, '2024');
  assert.strictEqual(r.disposals.length, 1);
  assert.strictEqual(r.disposals[0].symbol, 'C');
  assert.deepStrictEqual(r.available_years, [2025, 2024]); // full set stays discoverable
});

test('toCsv: header + one line per disposal, with CSV escaping', () => {
  const r = tax.buildReport([
    { symbol: 'BTC,USDT', direction: 'LONG', size_usd: 100, pnl: 5, fees: 0.1, opened_at: '2025-01-01T00:00:00Z', closed_at: '2025-01-02T00:00:00Z' },
  ]);
  const csv = tax.toCsv(r.disposals);
  const lines = csv.trim().split('\n');
  assert.match(lines[0], /^Symbol,Direction,Date Acquired,Date Sold,Proceeds/);
  assert.strictEqual(lines.length, 2);
  assert.match(lines[1], /^"BTC,USDT"/); // comma-bearing field is quoted
});

test('the report is labelled informational, not tax advice (§4)', () => {
  assert.match(tax.DISCLAIMER, /not tax advice/i);
  const r = tax.buildReport([]);
  assert.match(r.disclaimer, /not tax advice/i);
});

// ── Part 2: the /api/tax route against MemoryDB ──────────────────────────

function request(method, path, token) {
  return new Promise((resolve, reject) => {
    const req = http.request(`${base}${path}`, {
      method,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }, (res) => {
      let data = '';
      res.on('data', d => data += d);
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, raw: data }));
    });
    req.on('error', reject);
    req.end();
  });
}

let base, appServer, token;

test.before(async () => {
  const jwt = require('jsonwebtoken');
  const { pool } = require('../db');

  await pool.execute('INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['taxuser@test.io', 'x', 'Tax']);
  const [rows] = await pool.execute('SELECT id, email FROM users WHERE email = ?', ['taxuser@test.io']);
  const uid = rows[0].id;
  token = jwt.sign({ user_id: uid, email: rows[0].email }, process.env.JWT_SECRET);

  // Seed two closed trades (different years) via the 11-param closed insert.
  const ins = 'INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, pattern, opened_at, closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)';
  await pool.execute(ins, [uid, 'BTC/USDT:USDT', 'LONG', 100, 110, 200, 20, 0.3, 'breakout', '2025-01-01T00:00:00Z', '2025-01-05T00:00:00Z']);
  await pool.execute(ins, [uid, 'ETH/USDT:USDT', 'SHORT', 200, 190, 200, 10, 0.2, 'reversal', '2024-06-01T00:00:00Z', '2024-06-02T00:00:00Z']);

  const express = require('express');
  const app = express();
  app.use(express.json());
  app.use('/api/tax', require('../routes/tax'));
  await new Promise((resolve) => { appServer = app.listen(0, '127.0.0.1', resolve); });
  base = `http://127.0.0.1:${appServer.address().port}`;
});

test.after(() => { if (appServer) appServer.close(); });

test('GET /api/tax/report requires a JWT', async () => {
  const r = await request('GET', '/api/tax/report');
  assert.strictEqual(r.status, 401);
});

test('GET /api/tax/report returns the per-user realized-gains report', async () => {
  const r = await request('GET', '/api/tax/report', token);
  assert.strictEqual(r.status, 200);
  const body = JSON.parse(r.raw);
  assert.strictEqual(body.totals.disposals, 2);
  assert.strictEqual(body.totals.net_gain_loss, 30);
  assert.deepStrictEqual(body.available_years, [2025, 2024]);
  assert.match(body.disclaimer, /not tax advice/i);
});

test('GET /api/tax/report?year=2025 filters to the requested tax year', async () => {
  const r = await request('GET', '/api/tax/report?year=2025', token);
  const body = JSON.parse(r.raw);
  assert.strictEqual(body.scope, '2025');
  assert.strictEqual(body.totals.disposals, 1);
  assert.strictEqual(body.disposals[0].symbol, 'BTC/USDT:USDT');
});

test('GET /api/tax/export.csv downloads a CSV attachment', async () => {
  const r = await request('GET', '/api/tax/export.csv', token);
  assert.strictEqual(r.status, 200);
  assert.match(r.headers['content-type'], /text\/csv/);
  assert.match(r.headers['content-disposition'] || '', /attachment; filename=/);
  assert.match(r.raw, /^Symbol,Direction,Date Acquired/);
});
