'use strict';
/**
 * Agent mind-stream feed: bot-authed ingest -> bounded ring -> public read +
 * SSE 'activity' rebroadcast. The write side requires X-Bot-Secret; the read
 * side is public (it powers the landing page).
 */
process.env.JWT_SECRET = 'j'.repeat(64);
process.env.BOT_SYNC_SECRET = 's'.repeat(48);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const http = require('node:http');
const express = require('express');

let server, base;

function req(method, path, { botSecret, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const r = http.request(`${base}${path}`, {
      method,
      headers: {
        ...(botSecret ? { 'X-Bot-Secret': botSecret } : {}),
        ...(payload ? { 'Content-Type': 'application/json' } : {}),
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => resolve({ status: res.statusCode, data: d ? JSON.parse(d) : {} }));
    });
    r.on('error', reject);
    if (payload) r.write(payload);
    r.end();
  });
}

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/bot/sync', require('../routes/sync'));
  app.use('/api/feed', require('../routes/feed'));
  app.use('/api/stream', require('../routes/stream').router);
  await new Promise((res) => { server = app.listen(0, '127.0.0.1', res); });
  base = `http://127.0.0.1:${server.address().port}`;
});

test.after(() => { if (server) server.close(); });

test('feed ingest requires bot secret; sanitizes; public read returns newest first', async () => {
  // No secret -> rejected before any write.
  let r = await req('POST', '/api/bot/sync/events',
    { body: { events: [{ event_type: 'scan', title: 'nope' }] } });
  assert.strictEqual(r.status, 403);

  // Authed batch: junk type falls back to 'info', missing title is skipped,
  // oversized body is truncated server-side even if the bot didn't.
  r = await req('POST', '/api/bot/sync/events', {
    botSecret: process.env.BOT_SYNC_SECRET,
    body: { events: [
      { event_type: 'scan', severity: 'info', title: 'Scan complete — 60 pairs, 2 candidates',
        body: 'Strongest momentum: BTC, SOL', data: { pairs: 60, candidates: 2 },
        ts: '2026-07-16T10:00:00Z' },
      { event_type: 'thesis', severity: 'success', symbol: 'SOL/USDT:USDT',
        title: 'LONG SOL — confidence 78%', body: 'x'.repeat(2000),
        ts: '2026-07-16T10:01:00Z' },
      { event_type: 'DROP TABLE', title: 'weird type becomes info' },
      { event_type: 'scan', title: '' },                       // no title: skipped
    ] },
  });
  assert.strictEqual(r.status, 200);
  assert.strictEqual(r.data.inserted, 3);

  const read = await req('GET', '/api/feed/recent?limit=10');
  assert.strictEqual(read.status, 200);
  const events = read.data.events;
  assert.strictEqual(events.length, 3);
  // Newest-first by insertion order.
  assert.strictEqual(events[0].title, 'weird type becomes info');
  assert.strictEqual(events[0].event_type, 'info');
  assert.strictEqual(events[1].event_type, 'thesis');
  assert.ok(events[1].body.length <= 600);
  assert.strictEqual(events[2].data.pairs, 60);

  // Empty batch is a client error, not a silent no-op.
  r = await req('POST', '/api/bot/sync/events',
    { botSecret: process.env.BOT_SYNC_SECRET, body: { events: [] } });
  assert.strictEqual(r.status, 400);
});

test('ingest rebroadcasts each event as an SSE activity event', async () => {
  const chunks = [];
  const sse = await new Promise((resolve, reject) => {
    const r = http.get(`${base}/api/stream`, (res) => {
      res.on('data', c => chunks.push(c.toString()));
      resolve(res);
    });
    r.on('error', reject);
  });

  const posted = await req('POST', '/api/bot/sync/events', {
    botSecret: process.env.BOT_SYNC_SECRET,
    body: { events: [{ event_type: 'trade_close', severity: 'success',
      symbol: 'BTC/USDT:USDT', title: 'Closed BTC +$1.23' }] },
  });
  assert.strictEqual(posted.status, 200);

  // Give the stream a beat to deliver.
  await new Promise(res => setTimeout(res, 150));
  sse.destroy();
  const raw = chunks.join('');
  assert.ok(raw.includes('event: activity'), 'activity event on the stream');
  assert.ok(raw.includes('Closed BTC +$1.23'), 'payload rides the stream');
});
