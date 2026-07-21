/**
 * WEB-2: fixed-term staking on the web — route + panel contract.
 *
 * The lock flow's hard line (operator-only, double-confirm naming the lock
 * END date) is enforced by the bot gateway; these pins keep the web side
 * honest: validation before proxy, server-side identity, the 2FA gate for
 * enrolled accounts, and a UI whose final confirm echoes the exact date.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const route = read('routes/staking.js');
const dash = read('public/js/dashboard.js');

test('server mounts /api/staking', () => {
  assert.match(read('server.js'), /app\.use\('\/api\/staking', require\('\.\/routes\/staking'\)\)/);
});

test('route is JWT-authed, rate-limited, validates before the proxy call', () => {
  assert.match(route, /router\.use\(authMiddleware\)/);
  assert.match(route, /router\.post\('\/fixed', execLimit/);
  const validate = route.indexOf('bad_request');
  const proxy = route.indexOf("postGateway('/staking/fixed'");
  assert.ok(validate > 0 && proxy > validate);
});

test('enrolled accounts must present a fresh TOTP code to lock funds', () => {
  assert.match(route, /totp_enabled/);
  assert.match(route, /verifyTotp\(u\.totp_secret, code\)/);
  assert.match(route, /two_factor_required/);
});

test('identity is resolved server-side, never from the request body', () => {
  assert.match(route, /resolveBotIdentity\(req\)/);
  assert.ok(!route.includes('req.body.telegram_id'));
});

test('the final confirm echoes the exact lock END date', () => {
  assert.match(route, /confirm_lock_end/);
  assert.match(dash, /confirm_lock_end: sel\.lock_end/);
  // The UI states the no-early-redeem consequence in the confirm itself.
  assert.match(dash, /NOT redeemable until \$\{esc\(sel\.lock_end\)\} \(UTC\)/);
  assert.match(dash, /YES — lock until \$\{esc\(sel\.lock_end\)\}/);
});

test('a stale lock-end (409) re-shows live terms instead of retrying blind', () => {
  assert.match(dash, /status === 409/);
  assert.match(dash, /ayLock\.sel = null/);
});
