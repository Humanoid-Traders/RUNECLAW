'use strict';
/**
 * User-authored strategies → community marketplace. A member builds a strategy
 * from the intent-rule vocabulary, saves drafts, publishes; published strategies
 * appear on the public catalogue. Strict per-user isolation, per-user + publish
 * caps, and §4: a strategy is a CONFIG (rule chips + prose) — never a dollar
 * figure and never a stat/scorecard.
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
const store = require('../lib/user_strategies');

let server, base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authModule.router);
  app.use('/api/strategies', require('../routes/user_strategies'));
  app.use('/api/public/user-strategies', require('../routes/public_user_strategies'));
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});
test.after(() => { if (server) server.close(); });

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
  const r = await req('POST', '/api/auth/register', { body: { email: `strat${seq}@example.com`, password: 'longenough1' } });
  assert.equal(r.status, 200);
  return r.data.token;
}

const STRAT = {
  name: 'Mean-Reversion Majors', tagline: 'fade oversold majors', how: 'buy RSI<30 reclaims',
  icon: '🎯', regime: 'range', horizon: 'swing',
  rules: [
    { type: 'direction', value: 'both' }, { type: 'min_confidence', value: 60 },
    { type: 'min_rr', value: 1.8 }, { type: 'max_position_pct', value: 5 },
    { type: 'allowed_symbols', value: 'btc, eth, sol' },
  ],
};

test('lib validation: normalizes confidence, sanitizes symbols, rejects junk, no dollars', () => {
  const v = store.validateStrategy(STRAT);
  assert.ok(v.ok);
  const conf = v.data.rules.find(r => r.type === 'min_confidence');
  assert.equal(conf.value, 0.6);                        // 60% → 0.6
  const syms = v.data.rules.find(r => r.type === 'allowed_symbols');
  assert.deepEqual(syms.value, ['BTC', 'ETH', 'SOL']);  // upper + split
  assert.ok(!store.validateStrategy({ name: '', rules: [] }).ok);
  assert.ok(!store.validateStrategy({ name: 'x', rules: [{ type: 'max_position_pct', value: 900 }] }).ok);
  assert.ok(!store.validateStrategy({ name: 'ok', rules: [{ type: 'nonsense', value: 1 }] }).ok);
  assert.ok(!JSON.stringify(v.data).includes('$'));      // §4: no dollar figure anywhere
});

test('full flow: create draft → publish → appears on public catalogue → unpublish hides it', async () => {
  const token = await newUser();
  const c = await req('POST', '/api/strategies', { token, body: STRAT });
  assert.equal(c.status, 200);
  const slug = c.data.slug;
  assert.match(slug, /^mean-reversion-majors-[0-9a-f]{6}$/);

  const mine = await req('GET', '/api/strategies', { token });
  assert.equal(mine.data.strategies.length, 1);
  assert.equal(mine.data.strategies[0].visibility, 'draft');
  const id = mine.data.strategies[0].dbId;

  // Draft is NOT public yet.
  let pub = await req('GET', '/api/public/user-strategies');
  assert.ok(!(pub.data.agents || []).some(a => a.slug === slug));

  // Publish → shows up on the public catalogue with rules, no dollars/scorecard.
  const p = await req('POST', `/api/strategies/${id}/publish`, { token });
  assert.equal(p.status, 200);
  pub = await req('GET', '/api/public/user-strategies');
  const card = (pub.data.agents || []).find(a => a.slug === slug);
  assert.ok(card, 'published strategy is on the public catalogue');
  assert.equal(card.community, true);
  assert.ok(Array.isArray(card.rules) && card.rules.length);
  assert.ok(!('scorecard' in card), 'no scorecard on a user strategy');
  assert.ok(!JSON.stringify(pub.data).includes('$'), 'no dollar figures on the public surface');

  // Unpublish → gone from public.
  await req('POST', `/api/strategies/${id}/unpublish`, { token });
  pub = await req('GET', '/api/public/user-strategies');
  assert.ok(!(pub.data.agents || []).some(a => a.slug === slug));
});

test('per-user isolation: you cannot edit or delete another user\'s strategy', async () => {
  const a = await newUser(); const b = await newUser();
  const c = await req('POST', '/api/strategies', { token: a, body: STRAT });
  const id = (await req('GET', '/api/strategies', { token: a })).data.strategies[0].dbId;
  assert.ok(id);
  const del = await req('DELETE', `/api/strategies/${id}`, { token: b });
  assert.equal(del.status, 404);
  const upd = await req('PUT', `/api/strategies/${id}`, { token: b, body: STRAT });
  assert.equal(upd.status, 404);
  // owner still has it
  assert.equal((await req('GET', '/api/strategies', { token: a })).data.strategies.length, 1);
});

test('publish cap: a user can publish at most MAX_PUBLIC_PER_USER', async () => {
  const token = await newUser();
  const ids = [];
  for (let i = 0; i < store.MAX_PUBLIC_PER_USER + 1; i++) {
    await req('POST', '/api/strategies', { token, body: { ...STRAT, name: `S${i}` } });
  }
  const mine = (await req('GET', '/api/strategies', { token })).data.strategies;
  let published = 0, capHit = false;
  for (const s of mine) {
    const r = await req('POST', `/api/strategies/${s.dbId}/publish`, { token });
    if (r.status === 200) published++; else capHit = true;
  }
  assert.equal(published, store.MAX_PUBLIC_PER_USER);
  assert.ok(capHit, 'the cap is enforced');
});

test('auth required: the builder API rejects anonymous callers', async () => {
  const r = await req('GET', '/api/strategies');
  assert.ok(r.status === 401 || r.status === 403);
});

test('surfaces wired: server mounts, in-app builder, public marketplace cards', () => {
  const server_js = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(server_js, /\/api\/strategies.*require\('\.\/routes\/user_strategies'\)/);
  assert.match(server_js, /\/api\/public\/user-strategies.*require\('\.\/routes\/public_user_strategies'\)/);
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /Build your own strategy/);
  assert.match(dash, /\/api\/strategies/);
  assert.match(dash, /loadCommunity/);
  const agents = fs.readFileSync(path.join(__dirname, '..', 'public', 'agents.html'), 'utf8');
  assert.match(agents, /\/api\/public\/user-strategies/);
  assert.match(agents, /a\.community/);
});
