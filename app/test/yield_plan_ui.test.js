'use strict';
/**
 * CROSS-2 guided yield-execution preview — the admin dashboard surface.
 *
 * Source-asserted: an admin-only panel that posts a stables move to the
 * read-only /api/web3/cross-plan preview, renders the triple-gate verdict
 * (scanner + policy + authority + stables-only), and NEVER signs client-side —
 * execution stays the separate, gated testnet-signer call.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the yield-plan preview is mounted admin-only, next to the signer', () => {
  assert.match(dash, /function mountYieldPlanPreview\(/);
  assert.match(dash, /id="p-yieldplan"/);
  // mounted in the same plan==='admin' branch that mounts the testnet signer.
  assert.match(dash, /mountTestnetSigner\(document\.getElementById\('p-signer'\)\);[\s\S]{0,400}mountYieldPlanPreview/);
});

test('it posts a move to the read-only cross-plan preview and shows the gates', () => {
  assert.match(dash, /fetchJSON\('\/api\/web3\/cross-plan'/);
  assert.match(dash, /to_chain:.*dest:/);
  // renders each of the triple-gate + the stables-only hard-gate.
  assert.match(dash, /g\.scanner/);
  assert.match(dash, /g\.policy/);
  assert.match(dash, /g\.authority/);
  assert.match(dash, /d\.stables_only_ok/);
  assert.match(dash, /EXECUTE|SKIP/);
});

test('the preview never signs client-side (execution is a separate gated call)', () => {
  const fn = dash.slice(dash.indexOf('function mountYieldPlanPreview('),
    dash.indexOf('function mountTestnetSigner('));
  assert.ok(!/signTransaction|sendRawTransaction|private_key|build_and_sign|broadcast/.test(fn),
    'preview only — no client-side signing in the yield-plan panel');
});

test('the dashboard.js cache-buster is bumped', () => {
  assert.match(html, /dashboard\.js\?v=\d\d+/);
});
