/**
 * Bot identity resolution for gateway-backed routes.
 *
 * Maps the JWT-authenticated website user to the identity the bot keys its
 * UserStore/portfolios on:
 *   - Telegram-linked account  -> their telegram_id (full feature set)
 *   - web-only account         -> "web:<user_id>" (auto-provisioned by the
 *     bot gateway as a PAPER-ONLY trader; structurally locked out of live)
 *
 * The identity is always resolved server-side from the DB — the browser can
 * never choose who it acts as. Live trading, exchange credentials and live
 * controls intentionally do NOT use this fallback: they stay Telegram-gated
 * (routes/controls.js, routes/credentials.js).
 */

const { pool } = require('../db');

async function resolveBotIdentity(req) {
  const uid = req.user.user_id;
  const [rows] = await pool.execute(
    'SELECT telegram_id, telegram_linked, email FROM users WHERE id = ?', [uid]);
  const u = rows[0];
  if (u && u.telegram_linked && u.telegram_id) {
    return { id: String(u.telegram_id), linked: true, email: u.email || '' };
  }
  return { id: `web:${uid}`, linked: false, email: (u && u.email) || '' };
}

module.exports = { resolveBotIdentity };
