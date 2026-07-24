'use strict';
/**
 * Today on RUNECLAW — the public daily digest. Assembled ONLY from data the
 * platform already holds (engine deepscan, signals tape, arena pulse,
 * season); anything missing is OMITTED, never invented. §4: pattern names +
 * confidence percent, counts and win rates — no dollar amounts anywhere.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { buildToday } = require('../lib/daily_rune');
const { pool } = require('../db');

test('buildToday: empty parts → an honest, nearly-empty digest', () => {
  const d = buildToday({});
  assert.ok(d.generated_at);
  assert.ok(!('top_pattern' in d));
  assert.ok(!('signals' in d));
  assert.ok(!('arena' in d));
});

test('buildToday: the highest-confidence engine pattern wins, floor applied', () => {
  const scan = { deepscan: { hits: [
    { symbol: 'BTC/USDT', chart_patterns: [{ name: 'Bull Flag', confidence: 0.62, signal: 'bullish' }] },
    { symbol: 'SOLUSDT', chart_patterns: [{ name: 'Elliott 5-Wave Impulse', confidence: 0.81, signal: 'bullish' }] },
  ] } };
  const d = buildToday({ scan });
  assert.equal(d.top_pattern.name, 'Elliott 5-Wave Impulse');
  assert.equal(d.top_pattern.symbol, 'SOLUSDT');
  // Below the floor → omitted rather than headline-ized.
  const weak = buildToday({ scan: { deepscan: { hits: [
    { symbol: 'X', chart_patterns: [{ name: 'Meh', confidence: 0.4 }] }] } } });
  assert.ok(!('top_pattern' in weak));
});

test('§4: the digest carries counts/percent/patterns — never dollar fields', () => {
  const d = buildToday({
    scan: { deepscan: { hits: [{ symbol: 'BTCUSDT', chart_patterns: [{ name: 'Flag', confidence: 0.7 }] }] } },
    signals: { created_today: 5, resolved_today: 3, wins_today: 2 },
    arena: { traders: 9, closes_24h: 21 },
  });
  const flat = JSON.stringify(d);
  for (const banned of ['pnl', 'balance', 'margin', 'equity', 'usd', 'vUSDT']) {
    assert.ok(!flat.includes(banned), `digest must not contain "${banned}"`);
  }
});

test('resolved-today reads only rows with resolved_at set (MockPool mirror)', async () => {
  const now = new Date();
  pool.signals.push(
    { id: 7001, symbol: 'A', pnl: 5, created_at: now, resolved_at: now },
    { id: 7002, symbol: 'B', pnl: null, created_at: now, resolved_at: null },
  );
  const [rows] = await pool.execute('SELECT pnl FROM signals WHERE resolved_at >= ?',
    [new Date(Date.now() - 3600_000)]);
  assert.equal(rows.length, 1, 'NULL resolved_at never passes the cutoff');
});

test('landing: the strip is hidden by default and refuses one-stat digests', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.match(html, /id="todayStrip" hidden/);
  assert.match(html, /\/api\/today/);
  assert.match(html, /if \(bits\.length < 2\) return;/);
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /\/api\/today/);
});
