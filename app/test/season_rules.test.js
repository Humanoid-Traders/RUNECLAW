'use strict';
/**
 * Season rule variants (season two) — a LIVE season may constrain opens
 * ("Iron Season: max 5×, majors only"), enforced server-side on manual opens
 * AND the practice-follow sweep; the refusal names the season. An ended or
 * upcoming season constrains nothing; no rules = open season.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');
const fs = require('fs');
const path = require('path');
const authModule = require('../auth');
const { validateSeason, checkSeasonRules } = require('../lib/arena_seasons');
const { setTickerFetcher } = require('../lib/tickers');
const { pool } = require('../db');

test('validateSeason parses and bounds the rule vocabulary', () => {
  const v = validateSeason({ name: 'Iron Season', starts_at: '2026-08-01', ends_at: '2026-09-01',
    rules: { max_leverage: '5', majors_only: true } });
  assert.ok(v.ok);
  assert.deepEqual(v.data.rules, { max_leverage: 5, majors_only: true });
  assert.equal(validateSeason({ name: 'Open', starts_at: '2026-08-01', ends_at: '2026-09-01' }).data.rules, null);
  assert.ok(!validateSeason({ name: 'Bad', starts_at: '2026-08-01', ends_at: '2026-09-01',
    rules: { max_leverage: 99 } }).ok);
});

test('checkSeasonRules: live-only, named refusals, JSON-string tolerant', () => {
  const live = { name: 'Iron', starts_at: new Date(Date.now() - 3600000), ends_at: new Date(Date.now() + 86400000),
    rules: JSON.stringify({ max_leverage: 5, majors_only: true }) };
  assert.ok(checkSeasonRules(live, { symbol: 'BTCUSDT', leverage: 5 }).ok);
  const lev = checkSeasonRules(live, { symbol: 'BTCUSDT', leverage: 10 });
  assert.ok(!lev.ok && /Iron rules: max 5×/.test(lev.error));
  const alt = checkSeasonRules(live, { symbol: 'PEPEUSDT', leverage: 2 });
  assert.ok(!alt.ok && /majors only/.test(alt.error));
  const ended = { ...live, starts_at: new Date(Date.now() - 2 * 86400000), ends_at: new Date(Date.now() - 3600000) };
  assert.ok(checkSeasonRules(ended, { symbol: 'PEPEUSDT', leverage: 20 }).ok, 'ended seasons bind nothing');
  assert.ok(checkSeasonRules(null, { symbol: 'PEPEUSDT', leverage: 20 }).ok);
});

let server, base;
let PRICES = { BTCUSDT: { price: 100, change: 0, volume: 1 }, PEPEUSDT: { price: 1, change: 0, volume: 1 } };
test.before(async () => {
  setTickerFetcher(async () => PRICES);
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/arena', require('../routes/arena'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); setTickerFetcher(null); });

function req(method, p, { token, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${p}`, { method, headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(payload ? { 'Content-Type': 'application/json' } : {}) } }, (res) => {
      let d = ''; res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test('a live rule season binds manual opens end-to-end and rides the season payload', async () => {
  const reg = await req('POST', '/api/auth/register', { body: { email: 'rules1@example.com', password: 'longenough1' } });
  const token = reg.data.token;
  pool.users.find((u) => u.email === 'rules1@example.com').plan = 'admin';
  const launch = await req('POST', '/api/arena/season', { token, body: {
    name: 'Iron Season', starts_at: new Date(Date.now() - 60000), ends_at: new Date(Date.now() + 86400000),
    rules: { max_leverage: 5, majors_only: true } } });
  assert.equal(launch.status, 200);
  const s = await req('GET', '/api/arena/season');
  assert.deepEqual(s.data.season.rules, { max_leverage: 5, majors_only: true });
  const tooHot = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 10 } });
  assert.equal(tooHot.status, 400);
  assert.match(tooHot.data.error, /Iron Season rules: max 5×/);
  const offList = await req('POST', '/api/arena/open', { token, body: { symbol: 'PEPEUSDT', direction: 'LONG', margin: 100, leverage: 2 } });
  assert.equal(offList.status, 400);
  assert.match(offList.data.error, /majors only/);
  const fine = await req('POST', '/api/arena/open', { token, body: { symbol: 'BTCUSDT', direction: 'LONG', margin: 100, leverage: 5 } });
  assert.equal(fine.status, 200);
});

test('the launcher + banner ship the rules surface', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'arena.html'), 'utf8');
  assert.match(html, /id="ssLev"/);
  assert.match(html, /id="ssMajors"/);
  assert.match(html, /majors only<\/span>/);
  assert.match(html, /max ' \+ Number\(s\.rules\.max_leverage\)/);
});
