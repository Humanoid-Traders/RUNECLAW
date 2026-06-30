const crypto = require('crypto');

// Auto-generate JWT_SECRET ONLY in ephemeral/dev mode.
// In production (or any deployment without EPHEMERAL=true), JWT_SECRET MUST be set
// via environment variables. auth.js will refuse to start if it's missing or < 32 chars.
if (!process.env.JWT_SECRET) {
  if (process.env.EPHEMERAL === 'true' || process.env.NODE_ENV !== 'production') {
    process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
    console.log('WARNING: JWT_SECRET auto-generated (ephemeral). Set it in env for persistent auth.');
    console.log('NOTE: This is only safe for single-process deployments. In clustered/multi-replica setups, all processes must share the same JWT_SECRET.');
  }
  // If production without EPHEMERAL=true, let auth.js enforce the fatal exit.
}
// RC-AUD-015: never ship a hardcoded sync secret — it grants write access to the
// /api/bot/sync endpoints (which overwrite trade/equity data). Require it to be
// provided via env in all modes; the bot and web app must share the same value.
// The previously committed default must be rotated — it is exposed in git history.
if (!process.env.BOT_SYNC_SECRET || process.env.BOT_SYNC_SECRET.length < 32) {
  console.error('FATAL: BOT_SYNC_SECRET must be set to a shared secret of >=32 chars (see .env.example).');
  process.exit(1);
}

const express = require('express');
const path = require('path');
const { migrate } = require('./db');
const { router: authRouter } = require('./auth');
const tradesRouter = require('./routes/trades');
const syncRouter = require('./routes/sync');
const marketRouter = require('./routes/market');
const signalsRouter = require('./routes/signals');
const credentialsRouter = require('./routes/credentials');

const app = express();

app.use(express.json({ limit: '1mb' })); // Cap payload size
app.use(express.static(path.join(__dirname, 'public')));

// Security headers
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  next();
});

// API routes
app.use('/api/auth', authRouter);
app.use('/api/trades', tradesRouter);
app.use('/api/bot/sync', syncRouter);
app.use('/api/market', marketRouter);
app.use('/api/signals', signalsRouter);
app.use('/api/credentials', credentialsRouter);

// SPA fallback - serve index.html for non-API routes
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.get('/dashboard', (req, res) => res.sendFile(path.join(__dirname, 'public', 'dashboard.html')));

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
})();
