'use strict';
/**
 * Alerts 2.0: recurring mode with cooldown re-arm, signal watch (specific
 * coin + watchlist), and the Aave health-factor tripwire on the slow
 * on-chain cadence. Alerts only ever notify — nothing here can act.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const alerts = require('../lib/alerts');
const defi = require('../lib/defi');

const sent = [];
const notify = async (payload, userIds) => { sent.push({ payload, userIds }); };

test.before(async () => {
  alerts.setTickerFetcher(async () => ({
    BTCUSDT: { price: 95000, change: -2, volume: 1e9 },
    SOLUSDT: { price: 150, change: 6.2, volume: 5e8 },
  }));
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['a2@example.com', 'x'.repeat(60)]);
});

async function userId() {
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['a2@example.com']);
  return rows[0].id;
}

test('parser: "whenever" arms recurring; health factor + signal phrases parse', () => {
  const r = alerts.parseAlertCommand('alert me whenever BTC drops below $90k');
  assert.equal(r.mode, 'recurring');
  assert.equal(r.metric, 'price');

  const hf = alerts.parseAlertCommand('warn me if my health factor drops below 1.5');
  assert.deepEqual(hf, { kind: 'create', base: 'DEFI', metric: 'health_factor', op: '<', threshold: 1.5, mode: 'once' });

  const sw = alerts.parseAlertCommand('tell me when a signal fires on my watchlist');
  assert.equal(sw.metric, 'signal');
  assert.equal(sw.base, 'WATCHLIST');
  assert.equal(sw.mode, 'recurring');

  const sc = alerts.parseAlertCommand('ping me whenever a new signal fires on SOL');
  assert.equal(sc.base, 'SOL');
});

test('recurring: fires, stays armed, respects the cooldown, re-fires after it', async () => {
  const uid = await userId();
  const r = await alerts.createAlert(uid, {
    base: 'SOL', metric: 'change_abs_24h', op: '>', threshold: 5, mode: 'recurring', cooldownMin: 5,
  });
  assert.ok(r.ok, r.error);

  sent.length = 0;
  assert.equal(await alerts.runOnce(notify), 1, 'first pass fires');
  const [rows] = await pool.execute('SELECT * FROM user_alerts WHERE user_id = ?', [uid]);
  const a = rows.find(x => x.metric === 'change_abs_24h');
  assert.equal(Number(a.active), 1, 'recurring alert stays armed');
  assert.match(sent[0].payload.body, /recurring/);

  assert.equal(await alerts.runOnce(notify), 0, 'inside cooldown → silent');

  // Age the last fire past the cooldown → fires again.
  await pool.execute(
    'UPDATE user_alerts SET triggered_at = ?, trigger_price = ? WHERE id = ? AND active = 1',
    [new Date(Date.now() - 6 * 60_000), 6.2, a.id]);
  assert.equal(await alerts.runOnce(notify), 1, 'past cooldown → re-fires');
});

test('one-shot alerts still disarm exactly once', async () => {
  const uid = await userId();
  const r = await alerts.createAlert(uid, {
    base: 'BTC', metric: 'price', op: '<', threshold: 96000, mode: 'once',
  });
  assert.ok(r.ok);
  await alerts.runOnce(notify);
  const [rows] = await pool.execute('SELECT * FROM user_alerts WHERE user_id = ?', [uid]);
  const a = rows.find(x => x.metric === 'price' && Number(x.threshold) === 96000);
  assert.equal(Number(a.active), 0, 'one-shot disarmed');
});

test('signal watch: pushes on a new watchlist signal, dedupes already-seen ones', async () => {
  const uid = await userId();
  await pool.execute(
    'INSERT INTO user_profiles (user_id, risk_pref, watchlist, prefs) VALUES (?, ?, ?, ?)',
    [uid, null, JSON.stringify(['PENDLEUSDT']), '{}']);
  const r = await alerts.createAlert(uid, { base: 'WATCHLIST', metric: 'signal', op: '>', threshold: 0, mode: 'recurring' });
  assert.ok(r.ok, r.error);

  sent.length = 0;
  assert.equal(await alerts.runOnce(notify), 0, 'no signals yet → silent');

  await pool.execute(
    `INSERT INTO signals (signal_key, symbol, direction, confidence, score,
       pattern, regime, entry_price, stop_loss, take_profit, rr, thesis,
       status, pnl, created_at, resolved_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ['sw-1', 'PENDLE/USDT', 'LONG', 0.82, 0.8, 'breakout', 'TREND_UP',
     3.4, 3.2, 3.9, 2.5, null, 'NEW', null, new Date().toISOString(), '']);
  assert.equal(await alerts.runOnce(notify), 1, 'new watchlist signal → push');
  assert.match(sent[0].payload.body, /LONG PENDLE/);
  assert.match(sent[0].payload.body, /82% confidence/);

  assert.equal(await alerts.runOnce(notify), 0, 'same signal never double-fires');
});

test('health-factor tripwire: slow cadence, fires below threshold via defi reads', async () => {
  const uid = await userId();
  await pool.execute('UPDATE users SET wallet_address = ? WHERE id = ?',
    ['0x' + 'ef'.repeat(20), uid]);
  // Fake DeFi reads: one thin Aave position at HF 1.2.
  defi.setProviderFactory(() => ({
    async getBalance() { return 0n; },
    async call(tx) {
      if (String(tx.to).toLowerCase() === defi.AAVE_POOLS.ethereum.toLowerCase()) {
        const w = v => BigInt(v).toString(16).padStart(64, '0');
        return '0x' + w(5000n * 10n ** 8n) + w(3000n * 10n ** 8n) + w(0)
          + w(8000) + w(7500) + w(BigInt(Math.round(1.2 * 1e18)));
      }
      return '0x' + '0'.repeat(64);
    },
    async getNetwork() { return { chainId: 0n }; },
    async resolveName(n) { return n; },
  }));
  process.env.WEB3_CHAINS = 'ethereum';

  const r = await alerts.createAlert(uid, {
    base: 'DEFI', metric: 'health_factor', op: '<', threshold: 1.5, mode: 'once',
  });
  assert.ok(r.ok, r.error);

  sent.length = 0;
  alerts.__testResetOnchainSweep();
  assert.equal(await alerts.runOnce(notify), 1, 'HF 1.2 < 1.5 → fires');
  assert.match(sent[0].payload.title, /DeFi risk/);
  assert.match(sent[0].payload.body, /1\.2/);
  delete process.env.WEB3_CHAINS;
});

test('health-factor alert without a linked wallet is refused honestly', async () => {
  await pool.execute('INSERT INTO users (email, password_hash) VALUES (?, ?)',
    ['nolink2@example.com', 'x'.repeat(60)]);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['nolink2@example.com']);
  const r = await alerts.createAlert(rows[0].id, {
    base: 'DEFI', metric: 'health_factor', op: '<', threshold: 1.5,
  });
  assert.equal(r.ok, false);
  assert.match(r.error, /Link a wallet first/);
});
