'use strict';
/** NB4 — the operator go-live runbook exists and keeps its hard lines. */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

test('on-chain go-live runbook exists with both steps and the hard lines', () => {
  const doc = fs.readFileSync(
    path.join(__dirname, '..', '..', 'docs', 'ONCHAIN_GOLIVE.md'), 'utf8');
  assert.match(doc, /\/anchor confirm/, 'anchor confirm flow');
  assert.match(doc, /registration-plan/, 'ERC-8257 plan flow');
  assert.match(doc, /0x265BB2DBFC0A8165C9A1941Eb1372F349baD2cf1/, 'canonical registry');
  assert.match(doc, /never hold a key, never sign, never broadcast/, 'non-custodial line');
  assert.match(doc, /free and open/, 'no pricing / open predicate');
  const dev = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'developers.html'), 'utf8');
  assert.match(dev, /ONCHAIN_GOLIVE\.md/, 'developers page points at the runbook');
});
