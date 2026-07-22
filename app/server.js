const crypto = require('crypto');
const path = require('path');

// JWT_SECRET resolution, most-stable-first:
//   1. Explicit JWT_SECRET env (recommended; required for multi-replica).
//   2. Derived deterministically from BOT_SYNC_SECRET (already a required
//      >=32-char env). Same env -> same secret on EVERY restart/redeploy, even
//      on hosts with ephemeral disks. This fixes the "site keeps logging me
//      out" failure mode: the previous file-persisted secret (data/.jwt_secret)
//      was wiped on every restart of an ephemeral host, minting a new secret
//      and invalidating every session.
//   3. Ephemeral random (dev only, when BOT_SYNC_SECRET is also missing —
//      the fatal check below exits shortly after anyway).
if (!process.env.JWT_SECRET) {
  if (process.env.BOT_SYNC_SECRET && process.env.BOT_SYNC_SECRET.length >= 32) {
    process.env.JWT_SECRET = crypto
      .createHmac('sha256', process.env.BOT_SYNC_SECRET)
      .update('runeclaw-jwt-signing-v1')
      .digest('hex');
    console.log('JWT_SECRET derived from BOT_SYNC_SECRET — sessions survive restarts. '
      + 'Set an explicit JWT_SECRET for multi-replica deployments.');
  } else if (process.env.EPHEMERAL === 'true' || process.env.NODE_ENV !== 'production') {
    process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
    console.log('WARNING: JWT_SECRET auto-generated (ephemeral) — sessions reset on restart.');
  }
  // If production without a derivable secret, auth.js enforces the fatal exit.
}
// RC-AUD-015: never ship a hardcoded sync secret — it grants write access to the
// /api/bot/sync endpoints (which overwrite trade/equity data). Require it to be
// provided via env in all modes; the bot and web app must share the same value.
// The previously committed default must be rotated — it is exposed in git history.
if (!process.env.BOT_SYNC_SECRET || process.env.BOT_SYNC_SECRET.length < 32) {
  console.error('FATAL: BOT_SYNC_SECRET must be set to a shared secret of >=32 chars (see .env.example).');
  process.exit(1);
}

const { auditConfig } = require('./lib/config_audit');
// Surface every silently-degrading config (SMTP, gateway/creds secrets,
// APP_BASE_URL, OAuth) once at boot — and, in production, refuse to start on a
// fatal (e.g. a set-but-malformed WEB_CREDS_KEY that would fail every exchange-
// key submission). Runs after the JWT/BOT_SYNC hard checks above.
auditConfig();

const express = require('express');
const { migrate } = require('./db');
const { router: authRouter } = require('./auth');
const tradesRouter = require('./routes/trades');
const syncRouter = require('./routes/sync');
const marketRouter = require('./routes/market');
const insightRouter = require('./routes/insight');
const signalsRouter = require('./routes/signals');
const credentialsRouter = require('./routes/credentials');
const controlsRouter = require('./routes/controls');
const chatRouter = require('./routes/chat');
const publicChatRouter = require('./routes/public_chat');
const webtradeRouter = require('./routes/webtrade');
const portfolioRouter = require('./routes/portfolio');
const leaderboardRouter = require('./routes/leaderboard');
const trackRouter = require('./routes/track');
const labRouter = require('./routes/lab');
const feedRouter = require('./routes/feed');
const reportsRouter = require('./routes/reports');
const profileRouter = require('./routes/profile');
const { router: streamRouter } = require('./routes/stream');

const app = express();

// Behind the deployment's reverse proxy, req.ip is the proxy's address unless
// Express is told to trust the X-Forwarded-For hop. Without this, every
// per-IP rate limiter (public market data, /mcp, login attempts) collapses
// into ONE shared bucket for all visitors — a single client can exhaust the
// global budget, and abuse can't be attributed to a source address.
app.set('trust proxy', 1);

// Security headers — BEFORE the static handler so every response (including
// static-served HTML) carries them. The CSP allows inline script/style (the
// pages use both) but pins script sources to this origin plus the Telegram
// login widget — an injected <script src> from anywhere else won't execute,
// which is the second line of defense behind the app's HTML escaping.
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' https://telegram.org",
  "style-src 'self' 'unsafe-inline'",
  // blob: is needed for the WebGL agent viewer (GLTF decodes embedded textures
  // to same-origin blob: URLs); it's ephemeral and same-origin, not a network
  // egress. worker-src blob: covers optional GLTF Draco/KTX2 decoders.
  "img-src 'self' data: blob:",
  "font-src 'self'",
  "connect-src 'self' blob:",
  "worker-src 'self' blob:",
  "frame-src https://oauth.telegram.org",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join('; ');
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader('Content-Security-Policy', CSP);
  // Meaningful only over HTTPS; browsers ignore it on plain HTTP (local dev).
  res.setHeader('Strict-Transport-Security', 'max-age=15552000');
  next();
});

// WEB-VISION: the chat route accepts optional image attachments (chart /
// position screenshots, base64), which exceed the 1mb default. Give ONLY
// /api/chat a larger parser — it runs first for that path, so the global 1mb
// parser below skips the already-parsed body; every other route stays at 1mb.
app.use('/api/chat', express.json({ limit: '7mb' }));
app.use(express.json({ limit: '1mb' })); // Cap payload size
// Cache policy: HTML must never be cached (deploys ship new markup that
// references version-tagged assets, e.g. /styles.css?v=2) — a cached HTML +
// stale-CSS mix renders the dashboard completely unstyled. Assets get a
// moderate cache; the ?v= query busts them on every deploy that changes them.
app.use(express.static(path.join(__dirname, 'public'), {
  maxAge: '1h',
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('.html')) res.setHeader('Cache-Control', 'no-cache');
    else if (/\.(css|js|woff2)$/.test(filePath)) res.setHeader('Cache-Control', 'public, max-age=86400');
  },
}));

// API routes
app.use('/api/auth', authRouter);
app.use('/api/trades', tradesRouter);
app.use('/api/bot/sync', syncRouter);
app.use('/api/market', marketRouter);
app.use('/api/insight', insightRouter);
app.use('/api/patterns', require('./routes/patterns'));
app.use('/api/macro', require('./routes/macro'));
// Readiness composes several read-only signals; mount the specific path BEFORE
// the /api/guardian prefix so it wins the match.
app.use('/api/guardian/readiness', require('./routes/guardian_readiness'));
app.use('/api/guardian', require('./routes/guardian'));
app.use('/api/signals', signalsRouter);
app.use('/api/credentials', credentialsRouter);
app.use('/api/controls', controlsRouter);
app.use('/api/chat', chatRouter);
app.use('/api/llm', require('./routes/llm'));
app.use('/api/staking', require('./routes/staking'));
app.use('/api/public/chat', publicChatRouter);
app.use('/api/trade', webtradeRouter);
app.use('/api/portfolio', portfolioRouter);
app.use('/api/leaderboard', leaderboardRouter);
app.use('/api/public/proofofpnl', require('./routes/public_proofofpnl'));
app.use('/api/public/leaderboard', require('./routes/public_leaderboard'));
app.use('/api/public/letter', require('./routes/public_letter'));
app.use('/api/public/agent', require('./routes/public_agent'));
app.use('/api/public/invite', require('./routes/public_invite'));
app.use('/api/public', trackRouter);
app.use('/api/lab', labRouter);
app.use('/api/feed', feedRouter);
app.use('/api/reports', reportsRouter);
app.use('/api/profile', profileRouter);
app.use('/api/push', require('./routes/push'));
app.use('/api/alerts', require('./routes/alerts'));
app.use('/api/replay', require('./routes/replay'));
app.use('/api/letter', require('./routes/letter'));
app.use('/api/wallet', require('./routes/wallet'));
app.use('/api/defi', require('./routes/defi'));
app.use('/api/networth', require('./routes/networth'));
app.use('/api/holdings', require('./routes/holdings'));
app.use('/api/idleyield', require('./routes/idleyield'));
app.use('/api/crossyield', require('./routes/cross_yield'));
app.use('/api/authority', require('./routes/authority'));
app.use('/api/sentry', require('./routes/sentry'));
app.use('/api/positions', require('./routes/positions'));
app.use('/api/news', require('./routes/news'));
app.use('/api/proofofpnl', require('./routes/proofofpnl'));
app.use('/api/share', require('./routes/share'));
app.use('/api/airdrops', require('./routes/airdrops'));
app.use('/api/exposure', require('./routes/exposure'));
app.use('/api/research', require('./routes/research'));
app.use('/mcp', require('./routes/mcp'));
// ERC-8257 tool surface (well-known manifest + invoke endpoint + operator
// registration plan) — mounted at root because /.well-known/ is absolute.
app.use(require('./routes/tool8257'));
app.use('/api/public/status', require('./routes/public_status'));
app.use('/api/nft', require('./routes/nft'));
app.use('/api/spot', require('./routes/spot'));
app.use('/api/tax', require('./routes/tax'));
app.use('/api/reputation', require('./routes/reputation'));
app.use('/api/counterparty', require('./routes/counterparty'));
app.use('/api/web3', require('./routes/web3_execute'));   // admin-only preview (mounted first: specific POST /execute)
app.use('/api/web3', require('./routes/web3'));
app.use('/api/dapps', require('./routes/dapps'));

// Single-host dev foot-gun: Express and the bot's gateway both default to
// port 8080. Warn loudly if they would collide.
if (!process.env.BOT_GATEWAY_URL && String(process.env.PORT || 8080) === '8080') {
  console.warn('WARNING: BOT_GATEWAY_URL is unset and PORT is 8080 — the bot gateway '
    + 'default (http://localhost:8080) points at THIS server. Set BOT_GATEWAY_URL '
    + 'to the bot process (aiohttp dashboard) address.');
}
app.use('/api/stream', streamRouter);

// SPA fallback - serve index.html for non-API routes (never cached: see above)
app.get('/', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'index.html')); });
app.get('/dashboard', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'dashboard.html')); });
// Account-management landing pages reached from email links.
app.get('/track', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'track.html')); });
app.get('/proof', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'proof.html')); });
app.get('/leaderboard', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'leaderboard.html')); });
app.get('/letter/:week?', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'letter.html')); });
app.get('/wallet-link', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'wallet-link.html')); });
app.get('/reset', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'reset.html')); });
app.get('/verify', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'verify.html')); });
app.get('/agent', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'agent.html')); });
app.get('/agent/:address', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'agent-card.html')); });
app.get('/developers', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'developers.html')); });
app.get('/status', (req, res) => { res.setHeader('Cache-Control', 'no-cache'); res.sendFile(path.join(__dirname, 'public', 'status.html')); });

// Error handler
app.use((err, req, res, next) => {
  console.error('Unhandled error:', err.message);
  res.status(500).json({ error: 'Internal server error' });
});

(async () => {
  try {
    await migrate();
    console.log('Database migrated successfully');
  } catch (err) {
    console.error('Migration failed:', err.message);
    process.exit(1);
  }

  const PORT = process.env.PORT || 8080;
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`RUNECLAW app running on port ${PORT}`);
  });

  // Custom "tell me when…" alert tripwires: evaluate active alerts against
  // public tickers once a minute (skips instantly when none are armed).
  require('./lib/alerts').startAlertEngine();

  // Weekly agent letter: hourly sweep lazily writes the letter for the last
  // completed ISO week and announces it once with a web push.
  require('./lib/letter').startLetterSweep();

  // Follow-the-agent: hourly watch on the public verifiable board; rank moves
  // push a digest to users who opted into the 'board' topic.
  require('./lib/board_watch').startBoardWatch();
})();
