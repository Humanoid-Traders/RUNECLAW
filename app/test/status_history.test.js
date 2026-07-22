'use strict';
/**
 * NB2: 24h uptime history for the /status page. Pure prune/bucketize helpers +
 * the in-memory recorder, plus worst-state-wins bucket colouring.
 */

const test = require('node:test');
const assert = require('node:assert');
const h = require('../lib/status_history');

const HOUR = 60 * 60 * 1000;

test('pruneOlderThan drops samples before the cutoff', () => {
  const s = [{ t: 100, status: 'ok' }, { t: 200, status: 'ok' }, { t: 300, status: 'ok' }];
  assert.deepStrictEqual(h.pruneOlderThan(s, 200).map((x) => x.t), [200, 300]);
  assert.deepStrictEqual(h.pruneOlderThan([], 0), []);
});

test('bucketize places samples into the right hourly bucket', () => {
  const now = 24 * HOUR;
  // one sample 30 min into the most-recent bucket (bucket 23)
  const samples = [{ t: now - HOUR / 2, status: 'ok' }];
  const buckets = h.bucketize(samples, now, 24, HOUR);
  assert.strictEqual(buckets.length, 24);
  assert.strictEqual(buckets[23].status, 'ok');
  assert.strictEqual(buckets[0].status, 'no_data'); // 24h ago, empty
});

test('worst status wins within a bucket', () => {
  const now = 2 * HOUR;
  const samples = [
    { t: now - HOUR + 1, status: 'ok' },
    { t: now - HOUR + 2, status: 'degraded' },
    { t: now - HOUR + 3, status: 'partial' },
  ];
  const buckets = h.bucketize(samples, now, 2, HOUR);
  assert.strictEqual(buckets[1].status, 'degraded'); // degraded beats partial beats ok
});

test('unknown status is treated as partial, never rounded up to ok', () => {
  const now = HOUR;
  const buckets = h.bucketize([{ t: now - 1, status: 'weird' }], now, 1, HOUR);
  assert.strictEqual(buckets[0].status, 'partial');
});

test('record prunes to a 24h window and feeds bucketize', () => {
  h._reset();
  const now = 100 * HOUR;
  h.record('ok', now - 25 * HOUR);   // older than 24h → pruned on next record
  h.record('degraded', now - HOUR / 2);
  const kept = h.samples();
  assert.strictEqual(kept.length, 1);
  assert.strictEqual(kept[0].status, 'degraded');
  const buckets = h.bucketize(kept, now, 24, HOUR);
  assert.strictEqual(buckets[23].status, 'degraded');
});

test('record ignores non-finite timestamps', () => {
  h._reset();
  h.record('ok', 'not-a-number');
  h.record('ok', undefined);
  assert.strictEqual(h.samples().length, 0);
});

test('uptimePct is healthy-buckets over non-empty buckets', () => {
  const buckets = [
    { status: 'no_data' }, { status: 'ok' }, { status: 'ok' },
    { status: 'degraded' }, { status: 'no_data' },
  ];
  assert.strictEqual(h.uptimePct(buckets), 66.7); // 2 of 3 seen
  assert.strictEqual(h.uptimePct([{ status: 'no_data' }]), null);
  assert.strictEqual(h.uptimePct([]), null);
});
