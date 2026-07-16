'use strict';
/**
 * Personal what-if replay: pure stake-scaling math over the agent's real
 * recorded closed trades, window/symbol filters, the chat intercept, and
 * the authed REST surface. Always labelled hypothetical.
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
const replay = require('../lib/replay');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/replay', require('../routes/replay'));
  app.use('/api/chat', require('../routes/chat'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;

  // Seed the operator's (user 1) closed-trade history — the same insert
  // shape sync.js uses (11 params, status CLOSED as a literal).
  const seed = [
    // symbol, direction, entry, exit, size, pnl, closed_at
    ['BTC/USDT', 'LONG', 100, 110, 1000, 100, '2026-07-01T10:00:00Z'],   // +10%
    ['SOL/USDT', 'SHORT', 200, 210, 1000, -50, '2026-07-02T10:00:00Z'],  // -5%
    ['ETH/USDT', 'LONG', 50, 55, 500, 25, '2026-07-03T10:00:00Z'],       // +5%
  ];
  for (const [sym, dir, entry, exit, size, pnl, closedAt] of seed) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price,
        size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [1, sym, dir, entry, exit, size, pnl, 1, null, closedAt, closedAt]);
  }
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
    body: { email: `replay${userSeq}@example.com`, password: 'longenough1' },
  });
  assert.equal(r.status, 200);
  return r.data.token;
}

// ── Pure math ────────────────────────────────────────────────────────────────

test('computeReplay: fixed-stake scaling of recorded returns', () => {
  const trades = [
    { pnl: 100, size_usd: 1000, symbol: 'BTC/USDT', closed_at: '2026-07-01T10:00:00Z' }, // +10%
    { pnl: -50, size_usd: 1000, symbol: 'SOL/USDT', closed_at: '2026-07-02T10:00:00Z' }, // -5%
    { pnl: 25, size_usd: 500, symbol: 'ETH/USDT', closed_at: '2026-07-03T10:00:00Z' },   // +5%
  ];
  const r = replay.computeReplay(trades, 1000);
  // $1k/trade: +100, -50, +50 → net +100.
  assert.equal(r.trades, 3);
  assert.equal(r.fixed.net_pnl_usd, 100);
  assert.equal(r.fixed.final_usd, 1100);
  assert.equal(r.fixed.return_pct, 10);
  assert.equal(r.wins, 2);
  assert.equal(r.win_rate_pct, 66.67);
  assert.equal(r.fixed.best_trade.symbol, 'BTC/USDT');
  assert.equal(r.fixed.best_trade.pnl_usd, 100);
  assert.equal(r.fixed.worst_trade.pnl_usd, -50);
  // Compound: 1000 * 1.10 * 0.95 * 1.05 = 1097.25
  assert.equal(r.compound.final_usd, 1097.25);
  assert.equal(r.curve.length, 3);
  assert.equal(r.curve[2].equity, 1100);
});

test('computeReplay: skips unusable rows, never guesses', () => {
  const r = replay.computeReplay([
    { pnl: 10, size_usd: 0, closed_at: '2026-07-01T10:00:00Z' },      // no size
    { pnl: null, size_usd: 100, closed_at: '2026-07-01T10:00:00Z' },  // no pnl
    { pnl: -20, size_usd: 100, symbol: 'X', closed_at: '2026-07-02T10:00:00Z' },
  ], 1000);
  assert.equal(r.trades, 1);
  assert.equal(r.skipped, 2);
  assert.equal(r.fixed.net_pnl_usd, -200);
});

test('computeReplay: a worse-than-total-loss record cannot lose more than the stake', () => {
  const r = replay.computeReplay(
    [{ pnl: -150, size_usd: 100, closed_at: '2026-07-01T10:00:00Z' }], 1000);
  assert.equal(r.fixed.net_pnl_usd, -1000);  // clamped at -100% of the stake
});

test('computeReplay: stake is clamped to sane bounds', () => {
  const t = [{ pnl: 10, size_usd: 100, closed_at: '2026-07-01T10:00:00Z' }];
  assert.equal(replay.computeReplay(t, 1).stake, 10);
  assert.equal(replay.computeReplay(t, 99e9).stake, 1000000);
});

// ── Store-backed run + filters ───────────────────────────────────────────────

test('runReplay: replays the seeded history with filters', async () => {
  const all = await replay.runReplay({ stake: 1000 });
  assert.equal(all.trades, 3);
  assert.equal(all.fixed.net_pnl_usd, 100);

  const btcOnly = await replay.runReplay({ stake: 1000, symbol: 'BTC' });
  assert.equal(btcOnly.trades, 1);
  assert.equal(btcOnly.fixed.net_pnl_usd, 100);

  // A window that predates every seeded close finds nothing... but days
  // are relative to NOW, so instead verify the all-time window keeps all.
  const windowed = await replay.runReplay({ stake: 1000, days: 0 });
  assert.equal(windowed.trades, 3);
});

// ── REST ─────────────────────────────────────────────────────────────────────

test('REST: authed replay with stake scaling; unauthenticated rejected', async () => {
  const anon = await req('GET', '/api/replay?stake=500');
  assert.equal(anon.status, 401);

  const token = await newUser();
  const r = await req('GET', '/api/replay?stake=500', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.hypothetical, true);
  assert.equal(r.data.trades, 3);
  // Half the stake → half the fixed PnL.
  assert.equal(r.data.fixed.net_pnl_usd, 50);
});

// ── Chat intercept ───────────────────────────────────────────────────────────

test('chat: "what if I\'d taken every signal with $1k?" replies with the replay', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', {
    token, body: { text: "What if I'd taken every signal with $1k?" },
  });
  assert.equal(r.status, 200);
  assert.equal(r.data.intent, 'replay');
  assert.match(r.data.reply_html, /What-if replay/);
  assert.match(r.data.reply_html, /\$1,000 on every agent trade/);
  assert.match(r.data.reply_html, /Net: <b>\$100<\/b>/);
  assert.match(r.data.reply_html, /Hypothetical/i);
});

test('chat: stake variants parse; non-replay text proxies onward', async () => {
  const token = await newUser();
  const r = await req('POST', '/api/chat', {
    token, body: { text: 'what if i traded every signal with $500' },
  });
  assert.equal(r.data.intent, 'replay');
  assert.match(r.data.reply_html, /\$500 on every agent trade/);
  assert.match(r.data.reply_html, /Net: <b>\$50<\/b>/);

  const other = await req('POST', '/api/chat', { token, body: { text: 'what if BTC dumps?' } });
  assert.equal(other.status, 503);   // falls through to the (unconfigured) bot proxy
});
