/**
 * sessionResponse() — the one builder every auth entry point now returns.
 *
 * Pins the bug it fixes: telegram_linked must reflect the ROW, not a per-route
 * hardcoded literal. Before consolidation, the Google/OAuth paths hardcoded
 * telegram_linked:false (and the OAuth callback omitted it), so a user who had
 * linked Telegram AND signed in with Google looked unlinked to the app and lost
 * live-control access. sessionResponse reads the flags back from the row.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);

const test = require('node:test');
const assert = require('node:assert');
const { pool } = require('../db');
const { sessionResponse } = require('../auth');

test('sessionResponse reflects the row flags, not a hardcoded literal', async () => {
  await pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['linked-google@test.io', 'x', 'LG']);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['linked-google@test.io']);
  const u = rows[0];
  // Simulate a Telegram-linked account (MemoryDB returns live row refs).
  u.telegram_linked = true;
  u.email_verified = true;

  // Pass ONLY {id} as the OAuth/Google paths do — the builder must re-read.
  const out = await sessionResponse({ id: u.id });
  assert.strictEqual(out.telegram_linked, true, 'must read telegram_linked from the row');
  assert.strictEqual(out.email_verified, true);
  assert.strictEqual(out.user_id, u.id);
  assert.ok(out.token, 'issues a JWT');
  assert.strictEqual(typeof out.equity, 'number');
});

test('sessionResponse merges per-route extras (e.g. OAuth provider markers)', async () => {
  await pool.execute(
    'INSERT INTO users (email, password_hash, name) VALUES (?, ?, ?)',
    ['extras@test.io', 'x', 'EX']);
  const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', ['extras@test.io']);
  const out = await sessionResponse({ id: rows[0].id }, { provider: 'discord', linked: true });
  assert.strictEqual(out.provider, 'discord');
  assert.strictEqual(out.linked, true);
  // Fresh account → flags default false, never undefined.
  assert.strictEqual(out.telegram_linked, false);
  assert.strictEqual(out.email_verified, false);
});
