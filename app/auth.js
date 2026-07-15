const express = require('express');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const { pool } = require('./db');
const mailer = require('./lib/mailer');
const oauth2 = require('./lib/oauth2');

const router = express.Router();

// CRITICAL: No fallback secret. Refuse to start if unset or too short.
const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET || JWT_SECRET.length < 32) {
  console.error('FATAL: JWT_SECRET must be set (>=32 chars). Refusing to start.');
  console.error('Generate one: node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'hex\'))"');
  process.exit(1);
}
// Session lifetime. Was '1h', which -- with no refresh-token flow ever built --
// logged users out every hour and broke every authenticated panel mid-session.
// Default to a generous 30d so day-to-day use stays signed in; operators who
// want shorter-lived tokens can set JWT_EXPIRY (e.g. '12h', '7d') in the env.
const JWT_EXPIRY = process.env.JWT_EXPIRY || '30d';

// OAuth providers (optional; each endpoint 503s cleanly when its secret is
// unset, so the site runs fine with only email/password until configured).
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN || process.env.BOT_TOKEN || '';
const GOOGLE_CLIENT_ID = process.env.GOOGLE_CLIENT_ID || '';
const TELEGRAM_AUTH_MAX_AGE_S = 86400; // reject widget payloads older than 24h

// Verify a Telegram Login Widget payload (pure; exported for tests).
// Per core.telegram.org/widgets/login#checking-authorization:
//   secret = SHA256(bot_token);  hash = HMAC_SHA256(data_check_string, secret)
// where data_check_string is the sorted "key=value" lines (excluding hash).
function verifyTelegramAuth(data, botToken, nowSec) {
  if (!data || !data.hash || !botToken) return false;
  const now = nowSec || Math.floor(Date.now() / 1000);
  const authDate = parseInt(data.auth_date, 10);
  if (!Number.isFinite(authDate) || now - authDate > TELEGRAM_AUTH_MAX_AGE_S) return false;
  const checkString = Object.keys(data)
    .filter((k) => k !== 'hash')
    .sort()
    .map((k) => `${k}=${data[k]}`)
    .join('\n');
  const secret = crypto.createHash('sha256').update(botToken).digest();
  const hmac = crypto.createHmac('sha256', secret).update(checkString).digest('hex');
  // Constant-time compare to avoid timing leaks.
  const a = Buffer.from(hmac, 'hex');
  const b = Buffer.from(String(data.hash), 'hex');
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

// Rate limiting: per-IP sliding window
const loginAttempts = new Map(); // ip -> { count, firstAttempt, lockedUntil }
const RATE_LIMIT_WINDOW = 15 * 60 * 1000; // 15 min
const RATE_LIMIT_MAX = 10;
const LOCKOUT_DURATION = 5 * 60 * 1000; // 5 min lockout after max attempts

// RC-AUD-026: per-account (per-email) failed-login throttle. The per-IP limiter
// above does not stop distributed / rotating-IP credential stuffing against a
// single account. This in-process counter mirrors the per-IP one, keyed by the
// normalized email, so repeated failures against one account lock it out
// regardless of source IP.
const accountAttempts = new Map(); // email -> { count, firstAttempt, lockedUntil }
const ACCOUNT_RATE_LIMIT_MAX = 8;

function _pruneAttemptMap(map) {
  const now = Date.now();
  for (const [key, entry] of map) {
    if (now - entry.firstAttempt > RATE_LIMIT_WINDOW && (!entry.lockedUntil || now > entry.lockedUntil)) {
      map.delete(key);
    }
  }
  // Cap map size to prevent unbounded growth
  if (map.size > 10000) {
    const entries = [...map.entries()].sort((a, b) => a[1].firstAttempt - b[1].firstAttempt);
    for (let i = 0; i < entries.length - 5000; i++) map.delete(entries[i][0]);
  }
}

function pruneRateLimits() {
  _pruneAttemptMap(loginAttempts);
  _pruneAttemptMap(accountAttempts);
}
const _pruneTimer = setInterval(pruneRateLimits, 60000);
if (_pruneTimer.unref) _pruneTimer.unref(); // don't hold the event loop open (matches lib/rate_limit.js)

function checkRateLimit(ip) {
  const now = Date.now();
  const entry = loginAttempts.get(ip);
  if (!entry) return true;
  if (entry.lockedUntil && now < entry.lockedUntil) return false;
  if (now - entry.firstAttempt > RATE_LIMIT_WINDOW) { loginAttempts.delete(ip); return true; }
  return entry.count < RATE_LIMIT_MAX;
}

function recordAttempt(ip) {
  const now = Date.now();
  const entry = loginAttempts.get(ip) || { count: 0, firstAttempt: now };
  entry.count++;
  if (entry.count >= RATE_LIMIT_MAX) entry.lockedUntil = now + LOCKOUT_DURATION;
  loginAttempts.set(ip, entry);
}

// RC-AUD-026: per-account counterparts to the per-IP helpers above.
function checkAccountLockout(email) {
  const now = Date.now();
  const entry = accountAttempts.get(email);
  if (!entry) return true;
  if (entry.lockedUntil && now < entry.lockedUntil) return false;
  if (now - entry.firstAttempt > RATE_LIMIT_WINDOW) { accountAttempts.delete(email); return true; }
  return entry.count < ACCOUNT_RATE_LIMIT_MAX;
}

function recordAccountFailure(email) {
  const now = Date.now();
  const entry = accountAttempts.get(email) || { count: 0, firstAttempt: now };
  entry.count++;
  if (entry.count >= ACCOUNT_RATE_LIMIT_MAX) entry.lockedUntil = now + LOCKOUT_DURATION;
  accountAttempts.set(email, entry);
}

function clearAccountFailures(email) {
  accountAttempts.delete(email);
}

// -- Middleware --

function authMiddleware(req, res, next) {
  const auth = req.headers.authorization;
  if (!auth || !auth.startsWith('Bearer ')) {
    return res.status(401).json({ error: 'Missing token' });
  }
  try {
    const payload = jwt.verify(auth.slice(7), JWT_SECRET);
    req.user = payload;
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid token' });
  }
}

// -- Helpers --

function signToken(user) {
  return jwt.sign({ user_id: user.id, email: user.email }, JWT_SECRET, { expiresIn: JWT_EXPIRY });
}

async function getUserEquity(userId) {
  // Use latest synced equity snapshot if available
  const [snapRows] = await pool.execute(
    'SELECT equity FROM equity_snapshots WHERE user_id = ? ORDER BY snapshot_at DESC LIMIT 1',
    [userId]
  );
  if (snapRows.length > 0) {
    return parseFloat(snapRows[0].equity);
  }
  // Fallback: compute from trade PnL (for users who haven't synced yet)
  const [rows] = await pool.execute(
    'SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM trades WHERE user_id = ? AND status = ?',
    [userId, 'CLOSED']
  );
  return 10000 + parseFloat(rows[0].total_pnl || 0);
}

// -- Account-management helpers (email verification + password reset) --

// Tokens are stored HASHED in the DB; only the raw token travels in the email
// link, so a DB leak can't be replayed to verify an address or reset a password.
function _hashToken(raw) {
  return crypto.createHash('sha256').update(raw).digest('hex');
}

const VERIFY_TTL_MS = 24 * 60 * 60 * 1000; // 24h
const RESET_TTL_MS = 30 * 60 * 1000;       // 30m

// Issue a verification token for a user and email the link. Best-effort: a
// mailer failure never breaks the calling flow (registration still succeeds).
async function sendVerificationEmail(userId, email) {
  try {
    const raw = crypto.randomBytes(32).toString('hex');
    const expires = new Date(Date.now() + VERIFY_TTL_MS);
    await pool.execute(
      'UPDATE users SET verify_token = ?, verify_token_expires = ? WHERE id = ?',
      [_hashToken(raw), expires, userId]
    );
    const base = mailer.baseUrl();
    const link = `${base}/verify?token=${raw}`;
    await mailer.sendMail({
      to: email,
      subject: 'Verify your RUNECLAW email',
      text: `Confirm your email to finish setting up RUNECLAW.\n\nOpen this link (valid 24h):\n${link}\n\nIf you didn't create an account, ignore this message.`,
      html: `<p>Confirm your email to finish setting up <b>RUNECLAW</b>.</p>`
        + `<p><a href="${link}">Verify my email</a> (valid 24h)</p>`
        + `<p style="color:#888;font-size:12px">If you didn't create an account, ignore this message.</p>`,
    });
    return true;
  } catch (err) {
    console.error('sendVerificationEmail error:', err.message);
    return false;
  }
}

// -- Routes --

router.post('/register', async (req, res) => {
  try {
    // Rate-limit registration per IP (reuses the login limiter) so the
    // endpoint can't be used for automated mass account creation — it was
    // the only unthrottled auth route (deep-audit finding).
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many attempts. Try again later.' });
    }
    recordAttempt(clientIp);

    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });
    // Validate email format
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ error: 'Invalid email format' });
    if (password.length < 10) return res.status(400).json({ error: 'Password must be at least 10 characters' });

    const normalizedEmail = email.trim().toLowerCase();
    const hash = await bcrypt.hash(password, 12);
    const [result] = await pool.execute(
      'INSERT INTO users (email, password_hash) VALUES (?, ?)',
      [normalizedEmail, hash]
    );
    const userId = result.insertId;

    const token = signToken({ id: userId, email: normalizedEmail });
    const equity = await getUserEquity(userId);
    // Best-effort verification email (no-op when SMTP unconfigured). Fired
    // before responding so the token is persisted; never blocks on delivery
    // errors (sendVerificationEmail swallows them).
    await sendVerificationEmail(userId, normalizedEmail);
    res.json({ token, user_id: userId, email: normalizedEmail, plan: 'free',
               telegram_linked: false, email_verified: false,
               email_pending: mailer.isConfigured(), equity });
  } catch (err) {
    // Uniform response to prevent user enumeration (don't reveal ER_DUP_ENTRY)
    if (err.code === 'ER_DUP_ENTRY') return res.status(400).json({ error: 'Registration failed. Please try a different email.' });
    console.error('Register error:', err.message);
    res.status(500).json({ error: 'Registration failed' });
  }
});

router.post('/login', async (req, res) => {
  try {
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many login attempts. Try again later.' });
    }

    const { email, password } = req.body;
    if (!email || !password) return res.status(400).json({ error: 'Email and password required' });

    const normalizedEmail = email.trim().toLowerCase();
    // RC-AUD-026: per-account lockout, in addition to the per-IP check above.
    if (!checkAccountLockout(normalizedEmail)) {
      return res.status(429).json({ error: 'Too many login attempts. Try again later.' });
    }

    const [rows] = await pool.execute('SELECT * FROM users WHERE email = ?', [normalizedEmail]);
    if (rows.length === 0) {
      recordAttempt(clientIp);
      recordAccountFailure(normalizedEmail);
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    const user = rows[0];
    const valid = await bcrypt.compare(password, user.password_hash);
    if (!valid) {
      recordAttempt(clientIp);
      recordAccountFailure(normalizedEmail);
      return res.status(401).json({ error: 'Invalid credentials' });
    }

    // Successful login — clear this account's failure counter.
    clearAccountFailures(normalizedEmail);
    const token = signToken(user);
    const equity = await getUserEquity(user.id);
    res.json({ token, user_id: user.id, email: user.email, plan: user.plan,
               telegram_linked: !!user.telegram_linked,
               email_verified: !!user.email_verified, equity });
  } catch (err) {
    console.error('Login error:', err.message);
    res.status(500).json({ error: 'Login failed' });
  }
});

router.get('/me', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, email, plan, telegram_linked, email_verified, password_hash FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    const equity = await getUserEquity(user.id);
    res.json({ user_id: user.id, email: user.email, plan: user.plan,
               telegram_linked: !!user.telegram_linked,
               email_verified: !!user.email_verified,
               has_password: !!user.password_hash, equity });
  } catch (err) {
    console.error('Me error:', err.message);
    res.status(500).json({ error: 'Failed to fetch user' });
  }
});

router.post('/link-token', authMiddleware, async (req, res) => {
  try {
    const token = crypto.randomBytes(16).toString('hex');
    const expires = new Date(Date.now() + 10 * 60 * 1000); // 10 min
    await pool.execute(
      'UPDATE users SET link_token = ?, link_token_expires = ? WHERE id = ?',
      [token, expires, req.user.user_id]
    );
    res.json({ token });
  } catch (err) {
    console.error('Link token error:', err.message);
    res.status(500).json({ error: 'Failed to generate token' });
  }
});

// -- Validate link token (called by the Telegram bot) --

router.post('/validate-token', async (req, res) => {
  try {
    const { token, chat_id } = req.body;
    if (!token || !chat_id) return res.status(400).json({ error: 'token and chat_id required' });

    // Find user with this token that hasn't expired
    const [rows] = await pool.execute(
      'SELECT id, email, plan FROM users WHERE link_token = ? AND link_token_expires > ?',
      [token, new Date()]
    );

    if (rows.length === 0) {
      return res.status(404).json({ error: 'Token invalid or expired' });
    }

    const user = rows[0];

    // Consume the token, mark telegram linked, and RECORD the telegram id so the
    // website can attach exchange-credential submissions to the right bot account.
    await pool.execute(
      'UPDATE users SET link_token = NULL, link_token_expires = NULL, telegram_linked = TRUE, telegram_id = ? WHERE id = ?',
      [String(chat_id).slice(0, 32), user.id]
    );

    res.json({ user_id: user.id, email: user.email, plan: user.plan });
  } catch (err) {
    console.error('Validate token error:', err.message);
    res.status(500).json({ error: 'Token validation failed' });
  }
});

// -- OAuth: find-or-create by provider identity --
// 1) match the provider id; 2) else link to an existing email account;
// 3) else create a passwordless account. Returns the user row (id, email, plan).
const _PROVIDER_ID_COLUMN = {
  google: 'google_id',
  telegram: 'telegram_id',
  discord: 'discord_id',
  x: 'x_id',
};

async function findOrCreateOAuthUser({ provider, providerId, email, avatarUrl }) {
  const idCol = _PROVIDER_ID_COLUMN[provider] || 'telegram_id';
  const [byId] = await pool.execute(
    `SELECT id, email, plan FROM users WHERE ${idCol} = ? LIMIT 1`, [providerId]);
  if (byId.length) return byId[0];

  if (email) {
    const [byEmail] = await pool.execute(
      'SELECT id, email, plan FROM users WHERE email = ? LIMIT 1', [email]);
    if (byEmail.length) {
      await pool.execute(`UPDATE users SET ${idCol} = ? WHERE id = ?`,
        [providerId, byEmail[0].id]);
      return byEmail[0];
    }
  }
  // Some providers give no email (Telegram, X) — synthesize a unique,
  // non-routable placeholder so the NOT-NULL/UNIQUE email column is satisfied;
  // the user can add a real one later. Keep Telegram's historical "tg-" prefix.
  const phUser = provider === 'telegram' ? 'tg' : provider;
  const phDomain = provider === 'telegram' ? 'telegram' : provider;
  const finalEmail = email || `${phUser}-${providerId}@${phDomain}.runeclaw.local`;
  const [result] = await pool.execute(
    `INSERT INTO users (email, ${idCol}, avatar_url, telegram_linked) VALUES (?, ?, ?, ?)`,
    [finalEmail, providerId, avatarUrl || null, provider === 'telegram']);
  return { id: result.insertId, email: finalEmail, plan: 'free' };
}

// -- Public provider config (no secrets) so the login page knows what to show --
router.get('/config', (_req, res) => {
  res.json({
    google_client_id: GOOGLE_CLIENT_ID || null,
    // Telegram widget needs the bot USERNAME (public). Set TELEGRAM_BOT_USERNAME
    // (without @). Only advertise Telegram login when the verifying token exists.
    telegram_bot: (TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_BOT_USERNAME) || null,
    // Redirect-based providers (Discord, X) — advertised only when configured.
    oauth_providers: oauth2.configuredProviders(),
    // Footer social links — operator-set, rendered only when present (no guessing).
    social_links: {
      x: process.env.SOCIAL_X_URL || null,
      discord: process.env.SOCIAL_DISCORD_URL || null,
      telegram: process.env.SOCIAL_TELEGRAM_URL || 'https://t.me/HTRUNECLAW_bot',
    },
  });
});

// -- Login / register with Telegram (Login Widget) --
router.post('/telegram', async (req, res) => {
  try {
    if (!TELEGRAM_BOT_TOKEN) return res.status(503).json({ error: 'Telegram login not configured' });
    const data = req.body || {};
    if (!verifyTelegramAuth(data, TELEGRAM_BOT_TOKEN)) {
      return res.status(401).json({ error: 'Telegram verification failed' });
    }
    const user = await findOrCreateOAuthUser({
      provider: 'telegram', providerId: String(data.id).slice(0, 32),
      email: null, avatarUrl: data.photo_url,
    });
    const token = signToken(user);
    const equity = await getUserEquity(user.id);
    res.json({ token, user_id: user.id, email: user.email, plan: user.plan || 'free',
               telegram_linked: true, equity });
  } catch (err) {
    console.error('Telegram auth error:', err.message);
    res.status(500).json({ error: 'Telegram login failed' });
  }
});

// -- Login / register with Google (Identity Services credential) --
router.post('/google', async (req, res) => {
  try {
    if (!GOOGLE_CLIENT_ID) return res.status(503).json({ error: 'Google login not configured' });
    const { credential } = req.body || {};
    if (!credential) return res.status(400).json({ error: 'Missing credential' });
    // Verify the ID token with Google (dep-free: the tokeninfo endpoint checks
    // signature + expiry for us; we still assert audience + verified email).
    const resp = await fetch(
      `https://oauth2.googleapis.com/tokeninfo?id_token=${encodeURIComponent(credential)}`);
    if (!resp.ok) return res.status(401).json({ error: 'Google verification failed' });
    const info = await resp.json();
    if (info.aud !== GOOGLE_CLIENT_ID) return res.status(401).json({ error: 'Token audience mismatch' });
    if (info.email_verified !== 'true' && info.email_verified !== true) {
      return res.status(401).json({ error: 'Google email not verified' });
    }
    const user = await findOrCreateOAuthUser({
      provider: 'google', providerId: String(info.sub),
      email: String(info.email).trim().toLowerCase(), avatarUrl: info.picture,
    });
    const token = signToken(user);
    const equity = await getUserEquity(user.id);
    res.json({ token, user_id: user.id, email: user.email, plan: user.plan || 'free',
               telegram_linked: false, equity });
  } catch (err) {
    console.error('Google auth error:', err.message);
    res.status(500).json({ error: 'Google login failed' });
  }
});

// -- Change password (authenticated) --
// If the account already has a password, the current one must be supplied and
// correct. OAuth-only accounts (no password_hash) can SET one without a current
// password — this is how a Google/Telegram user adds email/password login.
router.post('/change-password', authMiddleware, async (req, res) => {
  try {
    const { current_password, new_password } = req.body || {};
    if (!new_password || String(new_password).length < 10) {
      return res.status(400).json({ error: 'New password must be at least 10 characters' });
    }
    const [rows] = await pool.execute(
      'SELECT id, password_hash FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    if (user.password_hash) {
      if (!current_password) return res.status(400).json({ error: 'Current password required' });
      const ok = await bcrypt.compare(String(current_password), user.password_hash);
      if (!ok) return res.status(401).json({ error: 'Current password is incorrect' });
    }
    const hash = await bcrypt.hash(String(new_password), 12);
    await pool.execute('UPDATE users SET password_hash = ? WHERE id = ?', [hash, user.id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Change-password error:', err.message);
    res.status(500).json({ error: 'Failed to change password' });
  }
});

// -- Forgot password: issue a reset link (unauthenticated) --
// Always returns 200 with the same body regardless of whether the email exists,
// to avoid account enumeration. Rate-limited per IP (shared login limiter).
router.post('/forgot-password', async (req, res) => {
  const generic = { ok: true, message: 'If that email has an account, a reset link is on its way.' };
  try {
    const clientIp = req.ip || req.socket.remoteAddress || 'unknown';
    if (!checkRateLimit(clientIp)) {
      return res.status(429).json({ error: 'Too many attempts. Try again later.' });
    }
    recordAttempt(clientIp);

    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });
    const normalizedEmail = String(email).trim().toLowerCase();

    const [rows] = await pool.execute(
      'SELECT id, email, password_hash FROM users WHERE email = ?', [normalizedEmail]);
    // Only send for accounts that actually have a password to reset.
    if (rows.length && rows[0].password_hash) {
      const raw = crypto.randomBytes(32).toString('hex');
      const expires = new Date(Date.now() + RESET_TTL_MS);
      await pool.execute(
        'UPDATE users SET reset_token = ?, reset_token_expires = ? WHERE id = ?',
        [_hashToken(raw), expires, rows[0].id]);
      const link = `${mailer.baseUrl()}/reset?token=${raw}`;
      try {
        await mailer.sendMail({
          to: normalizedEmail,
          subject: 'Reset your RUNECLAW password',
          text: `Reset your RUNECLAW password using this link (valid 30 minutes):\n${link}\n\nIf you didn't request this, ignore this email — your password is unchanged.`,
          html: `<p>Reset your <b>RUNECLAW</b> password.</p>`
            + `<p><a href="${link}">Choose a new password</a> (valid 30 minutes)</p>`
            + `<p style="color:#888;font-size:12px">If you didn't request this, ignore this email — your password is unchanged.</p>`,
        });
      } catch (mailErr) {
        console.error('Reset email send failed:', mailErr.message);
      }
    }
    res.json(generic);
  } catch (err) {
    console.error('Forgot-password error:', err.message);
    // Still return the generic body — don't leak that something errored.
    res.json(generic);
  }
});

// -- Reset password with a token (unauthenticated) --
router.post('/reset-password', async (req, res) => {
  try {
    const { token, new_password } = req.body || {};
    if (!token || !new_password) return res.status(400).json({ error: 'token and new_password required' });
    if (String(new_password).length < 10) {
      return res.status(400).json({ error: 'Password must be at least 10 characters' });
    }
    const [rows] = await pool.execute(
      'SELECT id, email FROM users WHERE reset_token = ? AND reset_token_expires > ?',
      [_hashToken(String(token)), new Date()]);
    if (rows.length === 0) return res.status(400).json({ error: 'Reset link is invalid or expired' });

    const hash = await bcrypt.hash(String(new_password), 12);
    await pool.execute(
      'UPDATE users SET password_hash = ?, reset_token = NULL, reset_token_expires = NULL WHERE id = ?',
      [hash, rows[0].id]);
    // A successful reset clears any per-account lockout so the user can log in.
    clearAccountFailures(String(rows[0].email).trim().toLowerCase());
    res.json({ ok: true });
  } catch (err) {
    console.error('Reset-password error:', err.message);
    res.status(500).json({ error: 'Failed to reset password' });
  }
});

// -- Verify email with a token (unauthenticated; link from the email) --
router.post('/verify-email', async (req, res) => {
  try {
    const token = (req.body && req.body.token) || req.query.token;
    if (!token) return res.status(400).json({ error: 'token required' });
    const [rows] = await pool.execute(
      'SELECT id FROM users WHERE verify_token = ? AND verify_token_expires > ?',
      [_hashToken(String(token)), new Date()]);
    if (rows.length === 0) return res.status(400).json({ error: 'Verification link is invalid or expired' });
    await pool.execute(
      'UPDATE users SET email_verified = TRUE, verify_token = NULL, verify_token_expires = NULL WHERE id = ?',
      [rows[0].id]);
    res.json({ ok: true });
  } catch (err) {
    console.error('Verify-email error:', err.message);
    res.status(500).json({ error: 'Verification failed' });
  }
});

// -- Resend the verification email (authenticated) --
router.post('/send-verification', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute(
      'SELECT id, email, email_verified FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    if (rows[0].email_verified) return res.json({ ok: true, already_verified: true });
    if (String(rows[0].email).endsWith('@telegram.runeclaw.local')) {
      return res.status(400).json({ error: 'Add a real email address first' });
    }
    await sendVerificationEmail(rows[0].id, rows[0].email);
    res.json({ ok: true, sent: mailer.isConfigured() });
  } catch (err) {
    console.error('Send-verification error:', err.message);
    res.status(500).json({ error: 'Failed to send verification' });
  }
});

// -- Redirect-based OAuth2 (Discord, X) — login OR link to a logged-in account --
//
// Google/Telegram use client-side widgets; Discord and X need the server to
// drive the authorization-code round-trip. Two short-lived in-memory stores back
// it (single-replica, like the rate limiters above): oauthFlows holds the CSRF
// state + PKCE verifier + optional link target between /start and /callback;
// oauthLinkKeys is a one-time handoff so a logged-in browser can enter LINK mode
// without putting its bearer token in a redirect URL.
const oauthFlows = new Map();     // state -> { provider, verifier, linkUserId, exp }
const oauthLinkKeys = new Map();  // linkKey -> { userId, exp }
const OAUTH_FLOW_TTL_MS = 10 * 60 * 1000;
const OAUTH_LINK_TTL_MS = 5 * 60 * 1000;

function _sweepOauth() {
  const now = Date.now();
  for (const [k, v] of oauthFlows) if (v.exp < now) oauthFlows.delete(k);
  for (const [k, v] of oauthLinkKeys) if (v.exp < now) oauthLinkKeys.delete(k);
}
const _oauthTimer = setInterval(_sweepOauth, 60000);
if (_oauthTimer.unref) _oauthTimer.unref();

function _oauthRedirectUri(provider) {
  // Providers require an absolute, pre-registered redirect URI. Operators set
  // APP_BASE_URL and register `${APP_BASE_URL}/api/auth/oauth/<provider>/callback`.
  const base = (process.env.APP_BASE_URL || '').replace(/\/+$/, '');
  return `${base}/api/auth/oauth/${provider}/callback`;
}

// Authed: mint a one-time key so a logged-in user can LINK a provider to their
// existing account (rather than creating/logging into a separate one).
router.post('/oauth-link-token', authMiddleware, (req, res) => {
  const key = crypto.randomBytes(24).toString('hex');
  oauthLinkKeys.set(key, { userId: req.user.user_id, exp: Date.now() + OAUTH_LINK_TTL_MS });
  res.json({ link_key: key });
});

// Begin the redirect flow → 302 to the provider's consent screen.
router.get('/oauth/:provider/start', (req, res) => {
  const provider = req.params.provider;
  if (!oauth2.isProviderConfigured(provider)) {
    return res.status(503).send('This login provider is not configured.');
  }
  let linkUserId = null;
  const linkKey = req.query.link ? String(req.query.link) : '';
  const lk = linkKey ? oauthLinkKeys.get(linkKey) : null;
  if (lk && lk.exp > Date.now()) { linkUserId = lk.userId; oauthLinkKeys.delete(linkKey); }
  const state = oauth2.randomState();
  const { verifier, challenge } = oauth2.pkcePair();
  oauthFlows.set(state, { provider, verifier, linkUserId, exp: Date.now() + OAUTH_FLOW_TTL_MS });
  const url = oauth2.buildAuthorizeUrl(provider, {
    redirectUri: _oauthRedirectUri(provider), state, challenge,
  });
  res.redirect(url);
});

// Provider redirects back here with ?code&state. Exchange → profile → session,
// then hand the session to the SPA via the URL fragment (never a query string,
// which would land the JWT in server/proxy access logs).
router.get('/oauth/:provider/callback', async (req, res) => {
  const provider = req.params.provider;
  const fail = (msg) => res.redirect(`/#oauth_error=${encodeURIComponent(msg)}`);
  try {
    const { code, state, error } = req.query;
    if (error) return fail(String(error));
    if (!code || !state) return fail('missing code or state');
    const flow = oauthFlows.get(String(state));
    if (!flow || flow.exp < Date.now() || flow.provider !== provider) {
      return fail('invalid or expired login attempt');
    }
    oauthFlows.delete(String(state));

    const accessToken = await oauth2.exchangeCode(provider, {
      code: String(code), redirectUri: _oauthRedirectUri(provider), verifier: flow.verifier,
    });
    const profile = await oauth2.fetchProfile(provider, accessToken);

    let user;
    if (flow.linkUserId) {
      // LINK mode: attach this provider identity to the already-logged-in user.
      const idCol = _PROVIDER_ID_COLUMN[provider];
      await pool.execute(`UPDATE users SET ${idCol} = ? WHERE id = ?`,
        [profile.providerId, flow.linkUserId]);
      const [rows] = await pool.execute(
        'SELECT id, email, plan FROM users WHERE id = ?', [flow.linkUserId]);
      user = rows[0];
    } else {
      user = await findOrCreateOAuthUser({
        provider, providerId: profile.providerId,
        email: profile.email, avatarUrl: profile.avatarUrl,
      });
    }
    if (!user) return fail('could not complete login');

    const token = signToken(user);
    const equity = await getUserEquity(user.id);
    const payload = Buffer.from(JSON.stringify({
      token, user_id: user.id, email: user.email, plan: user.plan || 'free',
      equity, provider, linked: Boolean(flow.linkUserId),
    })).toString('base64');
    res.redirect(`/#oauth=${payload}`);
  } catch (err) {
    console.error(`OAuth ${provider} callback error:`, err.message);
    return fail('login failed');
  }
});

module.exports = {
  router, authMiddleware, verifyTelegramAuth, findOrCreateOAuthUser, sendVerificationEmail,
};
