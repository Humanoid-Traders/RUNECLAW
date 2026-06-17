const express = require('express');
const path = require('path');
const { migrate } = require('./db');
const { router: authRouter } = require('./auth');
const tradesRouter = require('./routes/trades');

const app = express();

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// API routes
app.use('/api/auth', authRouter);
app.use('/api/trades', tradesRouter);

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

  app.listen(3000, '0.0.0.0', () => {
    console.log('RUNECLAW app running on port 3000');
  });
})();
