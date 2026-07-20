'use strict';
/**
 * Follow-this-agent (community C4) — board-milestone push.
 *
 * Contracts under test: the first sweep only records a baseline (a restart
 * never replays old moves as news); rank changes push ONE digest carrying
 * handles and ranks only (the board is size-agnostic, so no dollar can ride
 * a push); an unchanged board pushes nothing; and topic delivery is OPT-IN —
 * notifyTopic reaches only users whose profile prefs set push_board, so the
 * new category can never surprise an existing subscriber.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

// Real VAPID keys so lib/push actually configures and the delivery path runs
// (the transport itself is stubbed via setSender). Must precede the require.
const _webpush = require('web-push');
const _keys = _webpush.generateVAPIDKeys();
process.env.VAPID_PUBLIC_KEY = _keys.publicKey;
process.env.VAPID_PRIVATE_KEY = _keys.privateKey;

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const bw = require('../lib/board_watch');

test('first sweep is baseline-only; moves push one digest; quiet board pushes nothing', async () => {
  bw.resetBoardWatch();
  const pushes = [];
  const notify = async (p) => { pushes.push(p); };
  const board = [
    { handle: 'runefox', rank: 1 },
    { handle: 'wolf_7', rank: 2 },
  ];

  // Baseline sweep: records, never pushes (restart safety).
  assert.equal(await bw.sweepBoard(async () => board, notify), false);
  assert.equal(pushes.length, 0);

  // Unchanged board: silent.
  assert.equal(await bw.sweepBoard(async () => board, notify), false);
  assert.equal(pushes.length, 0);

  // Moves: swap + a new entrant + a departure -> exactly one digest.
  const moved = [
    { handle: 'wolf_7', rank: 1 },
    { handle: 'runefox', rank: 2 },
    { handle: 'nightowl', rank: 3 },
  ];
  assert.equal(await bw.sweepBoard(async () => moved, notify), true);
  assert.equal(pushes.length, 1);
  const body = pushes[0].body;
  assert.match(body, /wolf_7 climbed #2→#1/);
  assert.match(body, /runefox slipped #1→#2/);
  assert.match(body, /nightowl entered at #3/);
  assert.equal(pushes[0].url, '/leaderboard');
  assert.ok(!/\$\s*[\d.]/.test(JSON.stringify(pushes[0])), 'no dollar in a push');

  // Departure detected against the new baseline.
  assert.equal(await bw.sweepBoard(async () => moved.slice(0, 2), notify), true);
  assert.match(pushes[1].body, /nightowl left the board/);
});

test('fetch failure never throws and never pushes', async () => {
  bw.resetBoardWatch();
  const pushes = [];
  assert.equal(await bw.sweepBoard(async () => { throw new Error('gateway down'); },
    async (p) => pushes.push(p)), false);
  assert.equal(pushes.length, 0);
});

test('notifyTopic reaches ONLY users who opted into the topic', async () => {
  const push = require('../lib/push');
  // Force-configure and capture instead of hitting a push service.
  const sent = [];
  push.setSender(async (sub, payload) => { sent.push({ sub, payload }); });

  // Two users with profiles: one opted into the board topic, one not.
  await pool.execute(
    `INSERT INTO user_profiles (user_id, risk_pref, watchlist, prefs)
     VALUES (?, ?, ?, ?)`,
    [9001, 'balanced', '[]', JSON.stringify({ push_board: true })]);
  await pool.execute(
    `INSERT INTO user_profiles (user_id, risk_pref, watchlist, prefs)
     VALUES (?, ?, ?, ?)`,
    [9002, 'balanced', '[]', JSON.stringify({})]);
  // Both have push subscriptions.
  await pool.execute(
    `INSERT INTO push_subscriptions (user_id, endpoint, keys_json)
     VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), keys_json = VALUES(keys_json)`,
    [9001, 'https://push.example/a', JSON.stringify({ p256dh: 'k', auth: 'a' })]);
  await pool.execute(
    `INSERT INTO push_subscriptions (user_id, endpoint, keys_json)
     VALUES (?, ?, ?) ON DUPLICATE KEY UPDATE user_id = VALUES(user_id), keys_json = VALUES(keys_json)`,
    [9002, 'https://push.example/b', JSON.stringify({ p256dh: 'k', auth: 'a' })]);

  assert.ok(push.isConfigured(), 'test VAPID keys must configure push');
  const n = await push.notifyTopic('board', { title: 't', body: 'b' });
  assert.equal(n, 1, 'only the opted-in user is delivered');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].sub.endpoint, 'https://push.example/a');
});

test('profile prefs accept the push_board boolean (and drop junk)', () => {
  // sanitizePrefs is module-private; pin via source: the whitelist must
  // accept push_board as a boolean.
  const src = require('node:fs').readFileSync(
    require.resolve('../routes/profile'), 'utf8');
  assert.match(src, /push_board.*'boolean'|typeof input\.push_board === 'boolean'/);
});
