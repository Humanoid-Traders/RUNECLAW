'use strict';
/**
 * Guardian pre-trade review queue — web surface (route + dashboard card).
 *
 * The route is an admin-only relay to the bot gateway; the dashboard card is
 * read-only and admin-gated. Browser-only DOM code, so source-asserted: the
 * card exists, is mounted only for plan==='admin', reads /api/guardian/review,
 * and the surface never signs or broadcasts (the tighten math + admin re-check
 * live bot-side). The route forwards the resolved identity and a `tighten` spec
 * but never a broadcast flag.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'guardian_review.js'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('route relays GET review + POST tighten to the gateway (admin-authed)', () => {
  assert.match(route, /authMiddleware/);
  assert.match(route, /getGateway\([\s\S]*\/guardian\/review/);
  assert.match(route, /postGateway\('\/guardian\/review\/tighten'/);
  // it forwards a tighten spec + resolved identity, never a signer/broadcast flag.
  assert.match(route, /tighten:/);
  assert.ok(!/broadcast\s*:/.test(route), 'route must not forward a broadcast flag');
  assert.ok(!/signTransaction|sendRawTransaction|private_key/.test(route), 'route never signs');
});

test('route is mounted before the general /api/guardian prefix', () => {
  assert.match(server, /\/api\/guardian\/review['"], require\('\.\/routes\/guardian_review'\)/);
  const review = server.indexOf("/api/guardian/review");
  const general = server.indexOf("'/api/guardian', require");
  assert.ok(review > -1 && general > -1 && review < general, 'specific prefix must win');
});

test('dashboard has a read-only reviewQueueCard mounted admin-only', () => {
  assert.match(dash, /function reviewQueueCard\(/);
  assert.match(dash, /id="p-review"/);            // panel present (hidden by default)
  assert.match(dash, /hidden/);                   // starts hidden
  assert.match(dash, /\/api\/guardian\/review/);  // fed by the review endpoint
  // mounted inside the plan==='admin' branch (defense-in-depth; bot re-checks).
  assert.match(dash, /plan === 'admin'[\s\S]*reviewQueueCard/);
  assert.match(html, /dashboard\.js\?v=50/);      // cache-buster bumped
});

test('the review surface is presented as tighten-only / never authorizes', () => {
  assert.match(dash, /never authorize/);
  assert.match(dash, /Nothing here signs or broadcasts/);
});
