/**
 * Boot-time configuration audit — turn silent config degradation into a loud
 * signal.
 *
 * Many optional envs default to a no-op so the site still boots: unset SMTP
 * silently drops verification/reset emails, an unset WEB_GATEWAY_SECRET makes
 * web chat/trade return a mid-flow 503, an empty WEB_CREDS_KEY makes exchange-
 * key submission fail only when a user tries it, and a missing APP_BASE_URL
 * bakes malformed hosts into email links and OAuth redirect URIs. Each of those
 * failures is invisible at startup and only surfaces later as a broken user
 * flow.
 *
 * auditConfig() surfaces all of it ONCE at boot: a warning per degraded flow,
 * and — in production only — a hard finding for the cases that are security-
 * sensitive or that silently break a configured core flow. It is pure and takes
 * its env/logger by injection so it can be unit-tested without touching the real
 * process; server.js calls it with defaults and exits on a fatal in production.
 */

// A WEB_CREDS_KEY must decode (standard or url-safe base64) to exactly 32 bytes,
// matching lib/creds_crypto.loadKey — a set-but-malformed key is worse than an
// unset one because encryption throws at submit time with no boot signal.
function credsKeyState(raw) {
  if (!raw) return 'unset';
  const b64 = String(raw).replace(/-/g, '+').replace(/_/g, '/');
  let buf;
  try { buf = Buffer.from(b64, 'base64'); } catch { return 'invalid'; }
  return buf.length === 32 ? 'ok' : 'invalid';
}

function mailerConfigured(env) {
  return Boolean((env.SMTP_HOST || '').trim() && (env.MAIL_FROM || '').trim());
}

function oauthConfigured(env) {
  return Boolean(
    (env.GOOGLE_CLIENT_ID || '').trim()
    || ((env.DISCORD_CLIENT_ID || '').trim() && (env.DISCORD_CLIENT_SECRET || '').trim())
    || ((env.X_CLIENT_ID || '').trim() && (env.X_CLIENT_SECRET || '').trim()));
}

/**
 * @param {object} opts
 * @param {object} [opts.env=process.env]
 * @param {object} [opts.log=console]  needs .warn and .error
 * @param {function} [opts.onFatal]  called with the fatal findings in production
 *   (default: process.exit(1)); injected so tests don't kill the runner
 * @returns {Array<{level:'warn'|'fatal', key:string, msg:string}>}
 */
function auditConfig(opts = {}) {
  const env = opts.env || process.env;
  const log = opts.log || console;
  const prod = env.NODE_ENV === 'production';
  const findings = [];
  const warn = (key, msg) => findings.push({ level: 'warn', key, msg });
  const fatal = (key, msg) => findings.push({ level: 'fatal', key, msg });

  // Exchange-key encryption. Unset → the website connect form is simply off
  // (a warning). Set-but-malformed → it looks on but every submit throws, so in
  // production that is fatal: a user typing real API keys into a dead form is a
  // trust failure, not a graceful degrade.
  const credsKey = credsKeyState(env.WEB_CREDS_KEY);
  if (credsKey === 'unset') {
    warn('WEB_CREDS_KEY', 'unset — website exchange-key connect is disabled (submissions cannot be encrypted).');
  } else if (credsKey === 'invalid') {
    fatal('WEB_CREDS_KEY', 'set but not a 32-byte base64 key — every credential submission will fail at encrypt time.');
  }

  // Web gateway secret — the shared secret the chat/trade proxies present to the
  // bot. Unset/short → those routes 503. Degraded, not insecure → warn only.
  const gw = (env.WEB_GATEWAY_SECRET || '').trim();
  if (!gw) {
    warn('WEB_GATEWAY_SECRET', 'unset — web chat and web trade are disabled (routes return 503).');
  } else if (gw.length < 16) {
    warn('WEB_GATEWAY_SECRET', 'shorter than 16 chars — weak shared secret for the bot gateway.');
  }

  // Absolute base URL. Only matters once email or OAuth is configured — then a
  // missing host produces links like "/verify?token=…" and redirect URIs like
  // "/api/auth/oauth/discord/callback" with no origin, which silently 404 or
  // fail provider validation. Fatal in production when either is enabled.
  const base = (env.APP_BASE_URL || '').trim();
  if (!base) {
    if (mailerConfigured(env) || oauthConfigured(env)) {
      fatal('APP_BASE_URL', 'unset while email/OAuth is configured — verification links and OAuth redirect URIs are malformed.');
    } else {
      warn('APP_BASE_URL', 'unset — email links and OAuth redirects will be malformed once you enable those flows.');
    }
  }

  // Transactional email. Intentionally a no-op when unset, but the operator
  // should know verification + password-reset mail is silently not being sent.
  if (!mailerConfigured(env)) {
    warn('SMTP', 'SMTP_HOST/MAIL_FROM unset — verification and password-reset emails are NOT sent (flows no-op).');
  }

  // Bot analysis bridge — powers the market-insight panel. Silent localhost
  // default is fine in dev; worth a heads-up so a prod deploy is intentional.
  if (!(env.BOT_API_URL || '').trim()) {
    warn('BOT_API_URL', 'unset — the market-insight panel falls back to http://localhost:8000.');
  }

  for (const f of findings) {
    const line = `[config] ${f.key}: ${f.msg}`;
    if (f.level === 'fatal') log.error(`FATAL ${line}`);
    else log.warn(`WARNING ${line}`);
  }

  const fatals = findings.filter((f) => f.level === 'fatal');
  if (fatals.length && prod) {
    log.error(`[config] ${fatals.length} fatal configuration problem(s) in production — refusing to start.`);
    (opts.onFatal || (() => process.exit(1)))(fatals);
  }

  return findings;
}

module.exports = { auditConfig, credsKeyState, mailerConfigured, oauthConfigured };
