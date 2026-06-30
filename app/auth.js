const express = require('express');
const bcrypt = require('bcryptjs');
const jwt = require('jsonwebtoken');
const crypto = require('crypto');
const { pool } = require('./db');

const router = express.Router();

// CRITICAL: No fallback secret. Refuse to start if unset or too short.
const JWT_SECRET = process.env.JWT_SECRET;
if (!JWT_SECRET || JWT_SECRET.length < 32) {
  console.error('FATAL: JWT_SECRET must be set (>=32 chars). Refusing to start.');
  console.error('Generate one: node -e "console.log(require(\'crypto\').randomBytes(48).toString(\'hex\'))"');
  process.exit(1);
}
const JWT_EXPIRY = '1h'; // Shortened from 7d; use refresh tokens for longer sessions

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
setInterval(pruneRateLimits, 60000);

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

// -- Routes --

router.post('/register', async (req, res) => {
  try {
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

    // Seed demo trades for new user
    await seedDemoData(userId);

    const token = signToken({ id: userId, email: normalizedEmail });
    const equity = await getUserEquity(userId);
    res.json({ token, user_id: userId, email: normalizedEmail, plan: 'free', telegram_linked: false, equity });
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
    res.json({ token, user_id: user.id, email: user.email, plan: user.plan, telegram_linked: !!user.telegram_linked, equity });
  } catch (err) {
    console.error('Login error:', err.message);
    res.status(500).json({ error: 'Login failed' });
  }
});

router.get('/me', authMiddleware, async (req, res) => {
  try {
    const [rows] = await pool.execute('SELECT id, email, plan, telegram_linked FROM users WHERE id = ?', [req.user.user_id]);
    if (rows.length === 0) return res.status(404).json({ error: 'User not found' });
    const user = rows[0];
    const equity = await getUserEquity(user.id);
    res.json({ user_id: user.id, email: user.email, plan: user.plan, telegram_linked: !!user.telegram_linked, equity });
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

// -- Demo data seeder --

async function seedDemoData(userId) {
  const now = Date.now();
  const DAY = 86400000;
  const trades = [
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 67432.50, exit: 68891.20, size: 200, pnl: 43.24, fees: 0.41, pattern: 'hammer', daysAgo: 28 },
    { symbol: 'ETH/USDT', dir: 'LONG', entry: 3421.80, exit: 3510.40, size: 150, pnl: 38.85, fees: 0.31, pattern: 'engulfing', daysAgo: 26 },
    { symbol: 'SOL/USDT', dir: 'SHORT', entry: 172.40, exit: 168.10, size: 180, pnl: 44.92, fees: 0.36, pattern: 'evening_star', daysAgo: 24 },
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 68200.00, exit: 67800.50, size: 200, pnl: -11.72, fees: 0.40, pattern: 'doji', daysAgo: 22 },
    { symbol: 'LINK/USDT', dir: 'LONG', entry: 14.82, exit: 15.44, size: 120, pnl: 50.20, fees: 0.24, pattern: 'morning_star', daysAgo: 20 },
    { symbol: 'ETH/USDT', dir: 'SHORT', entry: 3580.20, exit: 3620.80, size: 150, pnl: -17.00, fees: 0.31, pattern: null, daysAgo: 18 },
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 69100.00, exit: 70250.30, size: 200, pnl: 33.28, fees: 0.42, pattern: 'three_soldiers', daysAgo: 16 },
    { symbol: 'SOL/USDT', dir: 'LONG', entry: 165.20, exit: 170.80, size: 180, pnl: 61.04, fees: 0.36, pattern: 'hammer', daysAgo: 14 },
    { symbol: 'BTC/USDT', dir: 'SHORT', entry: 70500.00, exit: 71200.40, size: 200, pnl: -19.86, fees: 0.42, pattern: null, daysAgo: 12 },
    { symbol: 'AVAX/USDT', dir: 'LONG', entry: 38.50, exit: 39.80, size: 140, pnl: 47.27, fees: 0.28, pattern: 'engulfing', daysAgo: 10 },
    { symbol: 'ETH/USDT', dir: 'LONG', entry: 3650.00, exit: 3720.50, size: 150, pnl: 28.97, fees: 0.31, pattern: 'morning_star', daysAgo: 8 },
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 71200.00, exit: 71850.20, size: 200, pnl: 18.25, fees: 0.43, pattern: 'hammer', daysAgo: 6 },
    { symbol: 'SOL/USDT', dir: 'SHORT', entry: 178.40, exit: 175.10, size: 180, pnl: 33.33, fees: 0.36, pattern: 'shooting_star', daysAgo: 5 },
    { symbol: 'LINK/USDT', dir: 'LONG', entry: 15.80, exit: 15.50, size: 120, pnl: -22.78, fees: 0.24, pattern: null, daysAgo: 4 },
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 72100.00, exit: 73050.80, size: 200, pnl: 26.36, fees: 0.43, pattern: 'engulfing', daysAgo: 3 },
    { symbol: 'ETH/USDT', dir: 'SHORT', entry: 3780.00, exit: 3740.20, size: 150, pnl: 15.79, fees: 0.31, pattern: 'evening_star', daysAgo: 2 },
    { symbol: 'AVAX/USDT', dir: 'LONG', entry: 41.20, exit: 40.80, size: 140, pnl: -13.59, fees: 0.28, pattern: null, daysAgo: 1 },
  ];

  for (const t of trades) {
    const opened = new Date(now - t.daysAgo * DAY);
    const closed = new Date(opened.getTime() + 3600000 * (1 + Math.random() * 8));
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, exit_price, size_usd, pnl, fees, status, pattern, opened_at, closed_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CLOSED', ?, ?, ?)`,
      [userId, t.symbol, t.dir, t.entry, t.exit, t.size, t.pnl, t.fees, t.pattern, opened, closed]
    );
  }

  // Add 2 open positions
  const openTrades = [
    { symbol: 'BTC/USDT', dir: 'LONG', entry: 73500.00, size: 200, sl: 72770, tp: 74380, fees: 0.22, pattern: 'hammer' },
    { symbol: 'ETH/USDT', dir: 'SHORT', entry: 3820.00, size: 150, sl: 3858, tp: 3770, fees: 0.16, pattern: 'shooting_star' },
  ];
  for (const t of openTrades) {
    await pool.execute(
      `INSERT INTO trades (user_id, symbol, direction, entry_price, size_usd, fees, status, pattern, stop_loss, take_profit)
       VALUES (?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?)`,
      [userId, t.symbol, t.dir, t.entry, t.size, t.fees, t.pattern, t.sl, t.tp]
    );
  }

  // Seed equity curve snapshots
  let equity = 10000;
  for (let i = 30; i >= 0; i--) {
    const snapDate = new Date(now - i * DAY);
    const matchingTrades = trades.filter(t => t.daysAgo >= i && t.daysAgo < i + 1);
    for (const t of matchingTrades) equity += t.pnl - t.fees;
    if (i % 1 === 0) {
      await pool.execute(
        'INSERT INTO equity_snapshots (user_id, equity, snapshot_at) VALUES (?, ?, ?)',
        [userId, Math.round(equity * 100) / 100, snapDate]
      );
    }
  }
}

module.exports = { router, authMiddleware };
