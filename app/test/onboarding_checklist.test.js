'use strict';
/**
 * Web AX Phase 3 (#257) — the Home "Getting started" onboarding checklist.
 *
 * dashboard.js is browser DOM code (no headless render), so we compile it to
 * prove it parses, then assert the checklist is wired to REAL account-state
 * endpoints and the supporting CSS ships. This pins the contract that the rail
 * derives progress only from /me, /credentials/status, /controls/status and
 * the portfolio — never invented — and that every ladder step is present.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const DASH = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const CSS = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('dashboard.js compiles (parse-only)', () => {
  assert.doesNotThrow(() => new vm.Script(DASH));
});

test('checklist is keyed to real account-state endpoints', () => {
  // The four real sources — no invented progress.
  assert.match(DASH, /\/api\/auth\/me/, 'reads /me for verified + telegram_linked');
  assert.match(DASH, /\/api\/credentials\/status/, 'reads exchange-connection status');
  assert.match(DASH, /\/api\/controls\/status/, 'reads live-eligibility + paused');
  assert.match(DASH, /email_verified/, 'uses real email-verification state');
  assert.match(DASH, /allowlisted/, 'go-live gate uses operator allowlist + live_enabled');
});

test('checklist renders all ladder steps + progress', () => {
  assert.match(DASH, /class="checklist"/, 'checklist list rendered');
  assert.match(DASH, /chk-progress/, 'progress meter present');
  for (const label of ['Verify your email', 'Place a paper trade',
    'Connect an exchange', 'Link Telegram', 'Go live']) {
    assert.ok(DASH.includes(label), `step present: ${label}`);
  }
  // Locked step (Go live needs connected keys) + paused-trading banner.
  assert.match(DASH, /locked:\s*!connected/, 'Go live locked until keys connected');
  assert.match(DASH, /onboard-banner/, 'paused-trading banner');
  // Collapses to a compact "all set" when every step is done.
  assert.match(DASH, /doneN === steps\.length/, 'collapses when fully set up');
});

test('CSS ships the checklist + banner rules', () => {
  assert.match(CSS, /\.checklist\s*{/, 'checklist layout');
  assert.match(CSS, /\.chk-item\s*{/, 'checklist item');
  assert.match(CSS, /\.chk-progressbar\s+span\s*{/, 'progress bar fill');
  assert.match(CSS, /\.onboard-banner\s*{/, 'paused banner style');
});
