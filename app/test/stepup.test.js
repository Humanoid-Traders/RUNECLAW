/**
 * SEC-1: per-action 2FA step-up on the live money-moves.
 *
 * A stolen web session (JWT in localStorage — the primary infostealer target)
 * must not be enough to move real money or unlock live trading. The pure
 * stepUpBlock() enforces a fresh TOTP code on a 2FA-enrolled account; the
 * routes gate the live-money actions with it, and the frontend prompts + retries
 * on the 401.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const { stepUpBlock } = require('../lib/stepup');
const totp = require('../lib/totp');

function validCode(secret) {
  return totp.hotp(secret, Math.floor(Date.now() / 30000));
}

test('non-enrolled account passes with no code (nothing to gate)', () => {
  assert.strictEqual(stepUpBlock(0, null, undefined, 'x'), null);
  assert.strictEqual(stepUpBlock(false, '', '', 'x'), null);
});

test('enrolled account with a missing or wrong code is blocked (fail-closed)', () => {
  const secret = totp.generateSecret();
  const noCode = stepUpBlock(1, secret, '', 'need code');
  assert.strictEqual(noCode.status, 401);
  assert.strictEqual(noCode.body.error, 'two_factor_required');
  assert.strictEqual(noCode.body.detail, 'need code');
  const bad = stepUpBlock(1, secret, '000000', 'need code');
  assert.ok(bad && bad.status === 401, 'a wrong code must be blocked');
});

test('enrolled account with a valid fresh code passes', () => {
  const secret = totp.generateSecret();
  assert.strictEqual(stepUpBlock(1, secret, validCode(secret), 'x'), null);
});

// ── the routes actually gate the live-money actions ────────────────────────
const routeSrc = (f) => fs.readFileSync(path.join(__dirname, '..', 'routes', f), 'utf8');

test('trade/confirm gates live-capable accounts with step-up', () => {
  const s = routeSrc('webtrade.js');
  assert.match(s, /stepUpBlock/);
  // AUDIT-FIX-3: step-up keys off the bot's AUTHORITATIVE live capability
  // (/trade/live_mode -> live_allowed), NOT the stale user_controls.live_enabled
  // mirror (which was empty for Telegram-/live and web-only live users, letting
  // their live confirms skip the code). Enrolled + live-capable is gated; paper
  // confirms stay frictionless.
  assert.match(s, /if \(urow\.totp_enabled\)/);
  assert.match(s, /trade\/live_mode/);
  assert.match(s, /live_allowed/);
  assert.doesNotMatch(s, /if \(urow\.live_enabled\)/);   // the buggy proxy is gone
});

test('controls gates ENABLING live (not disabling / de-risking) with step-up', () => {
  const s = routeSrc('controls.js');
  assert.match(s, /stepUpBlock/);
  assert.match(s, /if \(live === 1\)/);
  // The emergency /stop path must never require a code.
  const stopIdx = s.indexOf("router.post('/stop'");
  const stopBody = s.slice(stopIdx, stopIdx + 900);
  assert.ok(!/stepUpBlock/.test(stopBody), 'emergency stop must not be 2FA-gated');
});

test('staking still enforces step-up via the shared helper', () => {
  const s = routeSrc('staking.js');
  assert.match(s, /stepUpBlock/);
  assert.ok(!/verifyTotp/.test(s), 'staking should route through the shared helper, not inline verifyTotp');
});

// ── the frontend prompts + retries on the 401 ──────────────────────────────
test('app.js exposes postWithStepUp and it prompts on two_factor_required', () => {
  const s = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
  assert.match(s, /function postWithStepUp/);
  assert.match(s, /two_factor_required/);
  assert.match(s, /window\.prompt/);
  assert.match(s, /totp_code: code/);
  assert.match(s, /fetchJSON, postWithStepUp,/);  // exported on window.RC
});

test('the money-move call sites use postWithStepUp', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  assert.match(dash, /RC\.postWithStepUp\('\/api\/trade\/confirm'/);
  assert.match(dash, /RC\.postWithStepUp\('\/api\/controls'/);
  const chat = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
  assert.match(chat, /postWithStepUp\('\/api\/trade\/confirm'/);
});
