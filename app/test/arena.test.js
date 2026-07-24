'use strict';
/**
 * Paper Trading Arena — every registered user gets a virtual account with the
 * same starting stake, no exchange keys and no bot gateway. Covers the pure
 * engine (pnl / liquidation / equity / validation), the API (auto-provision,
 * open deducts margin, close realizes pnl, liquidations settle lazily,
 * per-user isolation) and the §4-safe public leaderboard (opt-in handles +
 * percent only — no balances, not even virtual ones).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const authModule = require('../auth');
const arena = require('../lib/arena');
const { setTickerFetcher } = require('../lib/tickers');

// ---- Pure engine --------------------------------------------------------

const near = (a, b, eps = 1e-6) => assert.ok(Math.abs(a - b) < eps, `${a} ≈ ${b}`);

test('posPnl: direction, leverage and the -margin floor', () => {
  const long = { direction: 'LONG', entry: 100, margin: 1000, leverage: 5 };
  near(arena.posPnl(long, 110), 500);                   // +10% × 5 on 1000
  near(arena.posPnl(long, 90), -500);
  near(arena.posPnl(long, 1), -1000);                   // clamped at -margin
  const short = { direction: 'SHORT', entry: 100, margin: 1000, leverage: 2 };
  near(arena.posPnl(short, 90), 200);
});

test('liqPrice + isLiquidated agree, both directions', () => {
  const long = { direction: 'LONG', entry: 100, margin: 100, leverage: 10 };
  const lp = arena.liqPrice(long);
  assert.ok(lp > 89 && lp < 91, `10x long from 100 liquidates near 90 (got ${lp})`);
  assert.ok(!arena.isLiquidated(long, lp + 0.1));
  assert.ok(arena.isLiquidated(long, lp - 0.1));
  const short = { direction: 'SHORT', entry: 100, margin: 100, leverage: 10 };
  const sp = arena.liqPrice(short);
  assert.ok(sp > 109 && sp < 111);
  assert.ok(arena.isLiquidated(short, sp + 0.1));
});

test('equity = balance + margin + unrealized; returnPct vs the uniform stake', () => {
  const pos = [{ symbol: 'BTCUSDT', direction: 'LONG', entry: 100, margin: 1000, leverage: 2 }];
  const eq = arena.equity(9000, pos, { BTCUSDT: { price: 110 } });
  assert.equal(eq, 9000 + 1000 + 200);
  assert.equal(arena.returnPct(10200), 2);
  assert.equal(arena.returnPct(arena.START_BALANCE), 0);
});

test('validateOpen enforces symbol, direction, margin, leverage and slots', () => {
  const ok = arena.validateOpen({ symbol: 'btcusdt', direction: 'long', margin: 100, leverage: 3 }, 5000, 0);
  assert.ok(ok.ok);
  assert.equal(ok.data.symbol, 'BTCUSDT');
  assert.ok(!arena.validateOpen({ symbol: 'x!', direction: 'LONG', margin: 100, leverage: 3 }, 5000, 0).ok);
  assert.ok(!arena.validateOpen({ symbol: 'BTCUSDT', direction: 'UP', margin: 100, leverage: 3 }, 5000, 0).ok);
  assert.ok(!arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 5, leverage: 3 }, 5000, 0).ok);
  assert.ok(!arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 6000, leverage: 3 }, 5000, 0).ok, 'margin > balance');
  assert.ok(!arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 21 }, 5000, 0).ok);
  assert.ok(!arena.validateOpen({ symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 3 }, 5000, arena.MAX_OPEN).ok, 'slots full');
});

// ---- API ---------------------------------------------------------------

let server, base;
let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };

test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/leaderboard', require('../routes/leaderboard'));
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, {
      method,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}
let seq = 0;
async function newUser() {
  seq++;
  const r = await req('POST', '/api/auth/register', { body: { email: `arena${seq}@example.com`, password: 'longenough1' } });
  assert.equal(r.status, 200);
  return r.data.token;
}

test('the account endpoint requires auth', async () => {
  const r = await req('GET', '/api/arena/account');
  assert.equal(r.status, 401);
});

test('first touch auto-provisions the uniform starting stake', async () => {
  const token = await newUser();
  const r = await req('GET', '/api/arena/account', { token });
  assert.equal(r.status, 200);
  assert.equal(r.data.balance, arena.START_BALANCE);
  assert.equal(r.data.equity, arena.START_BALANCE);
  assert.equal(r.data.return_pct, 0);
  assert.equal(r.data.virtual, true);
  assert.deepEqual(r.data.positions, []);
});

test('open fills at the live mark and deducts margin; close realizes pnl', async () => {
  const token = await newUser();
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const o = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 1000, leverage: 5 } });
  assert.equal(o.status, 200);
  assert.equal(o.data.filled.entry, 100);
  let a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.balance, 9000);                       // margin held by the position
  assert.equal(a.data.positions.length, 1);
  assert.equal(a.data.equity, 10000);                       // flat at entry
  // Price +10% → pnl = +500 on 1000 margin at 5x
  PRICES = { BTCUSDT: { price: 110, change: 10, volume: 1 } };
  a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions[0].pnl, 500);
  assert.equal(a.data.equity, 10500);
  const c = await req('POST', '/api/arena/close', { token, body: { position_id: a.data.positions[0].id } });
  assert.equal(c.status, 200);
  assert.equal(c.data.closed.pnl, 500);
  a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.balance, 10500);
  assert.equal(a.data.positions.length, 0);
  assert.equal(a.data.history[0].reason, 'manual');
});

test('a crossed liquidation settles lazily at -margin', async () => {
  const token = await newUser();
  PRICES = { ETHUSDT: { price: 100, change: 0, volume: 1 } };
  await req('POST', '/api/arena/open', { token, body: { symbol: 'ETHUSDT', direction: 'LONG', margin: 500, leverage: 10 } });
  PRICES = { ETHUSDT: { price: 80, change: -20, volume: 1 } };   // way past the ~90 liq
  const a = await req('GET', '/api/arena/account', { token });
  assert.equal(a.data.positions.length, 0, 'position was liquidated');
  assert.equal(a.data.history[0].reason, 'liquidated');
  assert.equal(a.data.history[0].pnl, -500);
  assert.equal(a.data.balance, 9500);
});

test('unknown symbols and broken market data are rejected honestly', async () => {
  const token = await newUser();
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  const bad = await req('POST', '/api/arena/open', { token, body: { symbol: 'NOPEUSDT', direction: 'LONG', margin: 100, leverage: 2 } });
  assert.equal(bad.status, 400);
});

test('users cannot close each other’s positions', async () => {
  const t1 = await newUser(); const t2 = await newUser();
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  await req('POST', '/api/arena/open', { token: t1, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 2 } });
  const a = await req('GET', '/api/arena/account', { token: t1 });
  const other = await req('POST', '/api/arena/close', { token: t2, body: { position_id: a.data.positions[0].id } });
  assert.equal(other.status, 404);
});

test('§4: the public leaderboard shows opt-in handles + percent only', async () => {
  const token = await newUser();
  PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 } };
  await req('GET', '/api/arena/account', { token });        // provision
  // invisible until the user opts in with a handle
  let lb = await req('GET', '/api/arena/leaderboard');
  const before = lb.data.rows.length;
  const opt = await req('POST', '/api/leaderboard/opt-in', { token, body: { handle: `arena_ace_${seq}` } });
  assert.equal(opt.status, 200);
  lb = await req('GET', '/api/arena/leaderboard');
  assert.equal(lb.status, 200);
  assert.equal(lb.data.rows.length, before + 1);
  const me = lb.data.rows.find((r) => r.handle === `arena_ace_${seq}`);
  assert.ok(me);
  assert.equal(typeof me.return_pct, 'number');
  // §4: no balance/equity/dollar keys on the public payload — percent + counts only
  const blob = JSON.stringify(lb.data).toLowerCase();
  for (const needle of ['balance', 'equity', 'email', 'user_id', '"usd', 'vusdt']) {
    assert.ok(!blob.includes(needle), `public board must not contain "${needle}"`);
  }
});

test('the /arena page is served, wired and honest about paper', () => {
  const fs = require('fs'); const path = require('path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /app\.get\('\/arena'/);
  assert.match(srv, /app\.use\('\/api\/arena'/);
  assert.match(html, /api\/arena\/account/);
  assert.match(html, /api\/arena\/leaderboard/);
  assert.match(html, /role="main"/);
  assert.match(html, /:focus-visible[^{]*\{[^}]*outline:/);
  assert.match(html, /rel="canonical" href="[^"]*\/arena"/);
  assert.match(html, /paper trading/i);
  assert.match(html, /no real funds ever move/i);
  assert.match(html, /not investment advice/i);
});
