'use strict';
/**
 * Restart-resilience for the /status trust page.
 *
 * On an ephemeral host every web redeploy wipes the in-memory scan cache, so
 * `latestScan` starts null — but the last engine push is still sitting in the
 * `scan_cache` DB row. Before this fix `getLatestScan()` returned the null
 * in-memory value, so the status page flashed a FALSE `no_data`/DEGRADED after
 * every deploy even though the engine was pushing normally.
 *
 * getLatestScan() must now rehydrate from `scan_cache` on cold start (mirroring
 * getLatestReports()), so the page reports the TRUE age — never rounded up to
 * healthy, but never a false no_data while the data is right there in the DB.
 */
const test = require('node:test');
const assert = require('node:assert');

test('getLatestScan rehydrates the last scan from scan_cache on cold start', async () => {
  delete process.env.DATABASE_URL;                 // force the in-memory MockPool
  const { pool } = require('../db');
  const sync = require('../routes/sync');

  const iso = new Date().toISOString();
  const scan = { received_at: iso, regime: 'TREND_UP', macro: { window: '24h' } };
  // Simulate a prior engine push that persisted to the DB, while the freshly
  // restarted process still has latestScan === null.
  await pool.execute('REPLACE INTO scan_cache (id, scan_json) VALUES (1, ?)', [JSON.stringify(scan)]);

  const got = await sync.getLatestScan();
  assert.ok(got, 'expected the scan to rehydrate from scan_cache, not stay null');
  assert.equal(got.received_at, iso, 'received_at must survive the restart');
  assert.equal(got.regime, 'TREND_UP', 'payload fields must survive the restart');

  // Idempotent: a second call serves the now-warm in-memory copy.
  const again = await sync.getLatestScan();
  assert.equal(again.received_at, iso);
});

test('the status probe wiring reads a real age from a rehydrated scan (no false no_data)', async () => {
  delete process.env.DATABASE_URL;
  const status = require('../lib/status');
  const NOW = Date.UTC(2026, 0, 1, 12, 0, 0);

  // A scan 5 minutes old (as if just rehydrated from the DB) must read `fresh`,
  // not `no_data`. This is exactly what getLatestScan now returns after a restart.
  status.setProbes({
    getScan: async () => ({ received_at: new Date(NOW - 5 * 60_000).toISOString() }),
    getReports: async () => ({ received_at: new Date(NOW - 30 * 60_000).toISOString() }),
    pingGateway: async () => ({ state: 'reachable' }),
    latestLetter: async () => ({ week_key: '2026-W01', generated_at: new Date(NOW - 3 * 86_400_000).toISOString() }),
    dbMode: () => 'memory',
    uptimeS: () => 42,
  });
  const s = await status.buildStatus(NOW);
  assert.equal(s.components.engine_scan.state, 'fresh');
  assert.notEqual(s.status, 'degraded');
});
