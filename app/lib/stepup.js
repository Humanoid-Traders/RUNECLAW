/**
 * Per-action 2FA step-up for money-moving / risk-changing web actions.
 *
 * A stolen web session (the primary infostealer target — the JWT lives in
 * localStorage) must not be enough to move real money or unlock live trading.
 * When an account has 2FA enrolled, the sensitive action must carry a fresh
 * TOTP code — the same verifier the login and fixed-term-staking flows use.
 *
 * This is a PURE check: the caller passes the `totp_enabled` / `totp_secret`
 * it already read from the user row (no extra DB round-trip), so it is trivial
 * to unit-test. Fail-CLOSED: an enrolled account with a missing or wrong code
 * is blocked; a non-enrolled account passes (nothing to check).
 */

const totp = require('./totp');

/**
 * @param {*} enrolled  users.totp_enabled (truthy = 2FA on)
 * @param {string} secret  users.totp_secret
 * @param {string} code  the client-supplied `totp_code`
 * @param {string} detail  human message for the 401 body
 * @returns {null | {status:number, body:object}} null = allowed; object = send it
 */
function stepUpBlock(enrolled, secret, code, detail) {
  if (!enrolled) return null;               // 2FA not enrolled — nothing to gate
  const c = String(code || '').trim();
  if (!c || !totp.verifyTotp(secret, c)) {
    return {
      status: 401,
      body: {
        error: 'two_factor_required',
        detail: detail || 'Enter your 6-digit authenticator code to continue.',
      },
    };
  }
  return null;                               // valid fresh code — allowed
}

module.exports = { stepUpBlock };
