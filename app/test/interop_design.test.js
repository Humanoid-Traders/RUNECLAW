'use strict';
/**
 * Agent interoperability design doc (PR MM) — pins that the doc exists, says
 * what it must say, and that "design-only" is structurally true: no payment
 * code rides along with it or sneaks in later without tripping this test.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.join(__dirname, '..', '..');

test('INTEROP.md exists, is design-only, and restates the hard lines', () => {
  const doc = fs.readFileSync(path.join(ROOT, 'docs', 'INTEROP.md'), 'utf8');
  assert.match(doc, /DESIGN ONLY/);
  assert.match(doc, /operator \+ legal review/);
  assert.match(doc, /No payment code ships/);
  assert.match(doc, /verify\s+artifacts, not reputations/i);
  assert.match(doc, /honestly UNVERIFIED/i);
  assert.match(doc, /No dollar amounts on public\/community surfaces/);
  assert.match(doc, /guided-only/);
  assert.match(doc, /heuristic flags, never verdicts/);
  assert.match(doc, /Non-custodial authority envelope/);
});

test('developers page points at the interop roadmap', () => {
  const html = fs.readFileSync(
    path.join(ROOT, 'app', 'public', 'developers.html'), 'utf8');
  assert.match(html, /docs\/INTEROP\.md/);
  assert.match(html, /design-only/i);
});

test('design-only is structurally true: no payment endpoints exist', () => {
  // Sweep the web routes for x402-style machinery — a payment implementation
  // must arrive through its own reviewed PR that consciously updates this
  // test, never as a rider.
  const routesDir = path.join(ROOT, 'app', 'routes');
  for (const f of fs.readdirSync(routesDir).filter(f => f.endsWith('.js'))) {
    const src = fs.readFileSync(path.join(routesDir, f), 'utf8');
    for (const marker of ['x402', 'X-PAYMENT', 'facilitator', 'payTo', 'status(402)']) {
      assert.ok(!src.includes(marker),
        `payment machinery marker "${marker}" found in routes/${f} — INTEROP.md gates are not cleared`);
    }
  }
});
