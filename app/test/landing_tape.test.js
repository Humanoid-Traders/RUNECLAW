'use strict';
/**
 * Landing-page live tape strip — the Arena's REAL latest closes on the front
 * door, reusing the public /api/arena/tape feed (#740). §4: percent + opt-in
 * handles + counts only. Honesty: a quiet tape keeps the strip hidden —
 * liveliness is never faked to visitors.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const html = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('the strip mounts hidden and is fed from the public tape API', () => {
  assert.match(html, /id="landingTape" hidden/);
  assert.match(html, /id="ltRows"/);
  assert.match(html, /id="ltPulse"/);
  assert.match(html, /\/api\/arena\/tape/);
});

test('a quiet tape keeps the strip hidden — no fake liveliness', () => {
  assert.match(html, /if \(!d \|\| !d\.rows \|\| !d\.rows\.length\) return;/);
  // Only revealed after real rows rendered.
  assert.match(html, /getElementById\('landingTape'\)\.hidden = false/);
});

test('§4: the strip shows percent, handle, counts — never dollar amounts', () => {
  const strip = html.slice(html.indexOf('// Live tape strip'), html.indexOf("/* strip is decoration"));
  assert.ok(strip.length > 200, 'strip script found');
  assert.match(strip, /toFixed\(2\) \+ '%/);       // percent rendering
  assert.match(strip, /t\.handle/);
  assert.match(strip, /traders/);                  // counts-only pulse line
  assert.ok(!/vUSDT|balance|margin|pnl\b/.test(strip), 'no dollar fields on the landing strip');
});

test('the strip caps at five rows and escapes user-controlled text', () => {
  assert.match(html, /d\.rows\.slice\(0, 5\)/);
  const strip = html.slice(html.indexOf('// Live tape strip'), html.indexOf("/* strip is decoration"));
  assert.match(strip, /esc\(t\.handle\)/);
  assert.match(strip, /esc\(t\.symbol\)/);
  assert.match(strip, /esc\(t\.reason\)/);
});
