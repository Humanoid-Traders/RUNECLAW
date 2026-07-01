/**
 * Real-time push (SSE) for the dashboard.
 *
 * The dashboard previously only learned about new scans/trades/signals by
 * polling on a timer (5-60s). This gives connected clients an immediate
 * "something changed, go re-fetch" nudge over one long-lived HTTP response.
 * No sensitive data rides on the stream itself -- clients still fetch the
 * real payload from the existing (possibly authed) REST endpoints; this is
 * just a public "refresh now" signal, same trust level as /api/bot/sync/scan.
 */

const express = require('express');

const router = express.Router();

const HEARTBEAT_MS = 25000;
// Bound total concurrent connections so a slow client leak (or a burst of
// tabs) can't exhaust server file descriptors.
const MAX_CLIENTS = 500;

const clients = new Set();

function broadcast(type, data) {
  if (clients.size === 0) return;
  const payload = `event: ${type}\ndata: ${JSON.stringify(data || {})}\n\n`;
  for (const res of clients) {
    try { res.write(payload); } catch (e) { clients.delete(res); }
  }
}

router.get('/', (req, res) => {
  if (clients.size >= MAX_CLIENTS) {
    return res.status(503).end();
  }
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no'); // disable nginx response buffering
  res.flushHeaders?.();
  res.write(': connected\n\n');

  clients.add(res);
  const hb = setInterval(() => {
    try { res.write(': ping\n\n'); } catch (e) { /* cleaned up on close */ }
  }, HEARTBEAT_MS);

  req.on('close', () => {
    clearInterval(hb);
    clients.delete(res);
  });
});

module.exports = { router, broadcast };
