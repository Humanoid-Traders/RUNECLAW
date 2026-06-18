const crypto = require('crypto');

// Auto-generate secrets for ephemeral/demo deployments if not set.
// In production, these MUST be set via environment variables.
if (!process.env.JWT_SECRET) {
  process.env.JWT_SECRET = crypto.randomBytes(48).toString('hex');
  console.log('WARNING: JWT_SECRET auto-generated (ephemeral). Set it in env for persistent auth.');
}
if (!process.env.BOT_SYNC_SECRET) {
  process.env.BOT_SYNC_SECRET = crypto.randomBytes(48).toString('hex');
  console.log('WARNING: BOT_SYNC_SECRET auto-generated (ephemeral). Set it in env for bot sync.');
}

const express = require('express');
const path = require('path');
const { migrate } = require('./db');
const { router: authRouter } = require('./auth');
const tradesRouter = require('./routes/trades');
const syncRouter = require('./routes/sync');
const marketRouter = require('./routes/market');

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
