'use strict';
/**
 * Venue overview + linking-friction fixes (from the UX audit):
 * - "Your venues — at a glance" joins per-venue STATUS (credentials/status)
 *   with per-venue EQUITY (holdings) in one card grid, hoisted high in the
 *   Portfolio view. Private surface (§4: dollars fine); unreadable honest.
 * - The onboarding ladder climbs in dependency order (Telegram BEFORE
 *   exchange keys, connect locked until linked) — the old order 409-dead-ended.
 * - The "/connect in Telegram" misdirections became in-app links to the key
 *   form that already existed; the 409 note hands over a next-step button.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const js = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const shell = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the unified venues panel joins status + equity per venue', () => {
  assert.match(js, /id="p-venues"/);
  assert.match(js, /Your venues — at a glance/);
  assert.match(js, /api\/credentials\/status/);
  assert.match(js, /api\/holdings/);
  assert.match(js, /applying…/);
  assert.match(js, /unreadable/);
  assert.match(js, /Manage keys →/);
});

test('the onboarding ladder climbs in dependency order', () => {
  const tg = js.indexOf("label: 'Link Telegram'");
  const ex = js.indexOf("label: 'Connect an exchange'");
  assert.ok(tg > 0 && ex > 0 && tg < ex, 'Telegram step precedes exchange step');
  const exBlock = js.slice(ex - 220, ex);
  assert.match(exBlock, /locked: !linked/);
});

test('no copy misdirects to Telegram for something the web can do', () => {
  assert.ok(!js.includes('/connect in Telegram'), 'misdirecting strings removed');
  assert.match(js, /connect keys here/);
  assert.match(js, /Link Telegram first →/);      // the 409 hands over a next step
});

test('cache-buster bumped so the fixes ship', () => {
  const v = Number((shell.match(/dashboard\.js\?v=(\d+)/) || [])[1]);
  assert.ok(v >= 93, `dashboard.js v>=93 (got ${v})`);
});
