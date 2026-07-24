'use strict';
/**
 * Pattern watch — the retention loop: when the engine's synced deep scan
 * reports a NEW high-confidence chart pattern on a symbol a user is holding,
 * that user gets ONE push. One source of truth (engine's deepscan block
 * only), owner-only delivery, per-(user,symbol,pattern) 12h dedupe, §4: no
 * amounts in the notification path.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const { transitions, MIN_CONFIDENCE, TTL_MS, MAX_PER_SYMBOL } = require('../lib/pattern_watch');

const HIT = (symbol, patterns) => ({ symbol, chart_patterns: patterns });
const PAT = (name, confidence, signal) => ({ name, confidence, signal: signal || 'bullish' });
const POS = (user_id, symbol, direction) => ({ user_id, symbol, direction: direction || 'LONG' });

test('a new high-confidence pattern on a held symbol notifies the holder once', () => {
  const hits = [HIT('BTC/USDT', [PAT('Elliott 5-Wave Impulse', 0.78)])];
  const positions = [POS(7, 'BTCUSDT')];
  const r1 = transitions(hits, positions, new Map(), 1000);
  assert.equal(r1.notify.length, 1);
  assert.equal(r1.notify[0].user_id, 7);
  assert.equal(r1.notify[0].name, 'Elliott 5-Wave Impulse');
  // Same sweep result two minutes later: deduped.
  const r2 = transitions(hits, positions, r1.seen, 1000 + 120000);
  assert.equal(r2.notify.length, 0);
  // After the TTL the pattern may announce again (a fresh read, not spam).
  const r3 = transitions(hits, positions, r2.seen, 1000 + TTL_MS + 1);
  assert.equal(r3.notify.length, 1);
});

test('confidence floor and per-symbol cap hold', () => {
  const hits = [HIT('BTCUSDT', [
    PAT('Weak Pattern', MIN_CONFIDENCE - 0.01),
    PAT('A', 0.9), PAT('B', 0.8), PAT('C', 0.7),
  ])];
  const r = transitions(hits, [POS(1, 'BTCUSDT')], new Map(), 1);
  const names = r.notify.map((n) => n.name);
  assert.ok(!names.includes('Weak Pattern'), 'below-floor pattern never pushes');
  assert.equal(names.length, MAX_PER_SYMBOL, 'top patterns only');
});

test('symbol forms normalize: BTC/USDT hit matches a BTCUSDT position', () => {
  const r = transitions([HIT('BTC/USDT', [PAT('Bull Flag', 0.7)])],
    [POS(3, 'BTCUSDT')], new Map(), 1);
  assert.equal(r.notify.length, 1);
});

test('only holders hear about it — no position, no push', () => {
  const r = transitions([HIT('BTCUSDT', [PAT('Bull Flag', 0.9)])],
    [POS(5, 'ETHUSDT')], new Map(), 1);
  assert.equal(r.notify.length, 0);
});

test('§4 + delivery: owner-only push, no amount fields in the event', () => {
  const r = transitions([HIT('SOLUSDT', [PAT('Double Bottom', 0.71)])],
    [POS(9, 'SOLUSDT', 'SHORT')], new Map(), 1);
  const ev = r.notify[0];
  for (const k of ['margin', 'balance', 'pnl', 'entry', 'equity', 'email']) {
    assert.ok(!(k in ev), `event must not carry "${k}"`);
  }
  const src = fs.readFileSync(path.join(__dirname, '..', 'lib', 'pattern_watch.js'), 'utf8');
  assert.match(src, /\[n\.user_id\]/);                    // owner-only delivery
  assert.match(src, /getLatestScan/);                     // engine block only
  const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server, /pattern_watch'\)\.startPatternWatch\(\)/);
});
