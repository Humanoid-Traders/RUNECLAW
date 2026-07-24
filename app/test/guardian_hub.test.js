'use strict';
/**
 * RUNECLAW Guardian hub (/guardian) — the umbrella page that ties the safety
 * modules into one story (Flight Recorder, Stress Lab, Systemic Risk Sentinel,
 * Intent Compiler) and de-clutters the header nav. §4: warns/explains/proves,
 * never moves funds; nothing is advice.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const gd = fs.readFileSync(path.join(__dirname, '..', 'public', 'guardian.html'), 'utf8');
const server = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the /guardian route is served', () => {
  assert.match(server, /app\.get\('\/guardian'/);
});

test('the hub links every module and states the principle', () => {
  for (const href of ['/flight', '/stress', '/sentinel', '/intent']) {
    assert.ok(gd.includes(`href="${href}"`), `hub links ${href}`);
  }
  // the guiding principle (propose → authorize → enforce → prove → recover)
  assert.match(gd, /proposes/); assert.match(gd, /authorize/);
  assert.match(gd, /enforces/); assert.match(gd, /proves/); assert.match(gd, /recovers/);
  // next modules are shown as in-development, not overstated as live
  assert.match(gd, /Transaction Firewall/);
  assert.match(gd, /Universal Escape Agent/);
  assert.match(gd, /investment advice/i);
  assert.match(gd, /revocable authority envelope/i);
});

test('Guardian is in the nav (header + footer) with an i18n label', () => {
  assert.match(i18n, /'nav\.guardian'/);
  assert.match(index, /data-i18n="nav\.guardian"/);   // header/menu
  assert.match(index, /href="\/guardian"/);
  // the individual module links still exist (now in the footer)
  assert.match(index, /href="\/flight"/);
  assert.match(index, /href="\/stress"/);
});

test('the module pages cross-link back to the hub', () => {
  for (const f of ['flight.html', 'stress.html', 'sentinel.html']) {
    const html = fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');
    assert.match(html, /href="\/guardian"/, `${f} links to /guardian`);
  }
});

test('the hub hero mounts the 3D Guardian orbit — six clickable modules around the core', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'guardian.html'), 'utf8');
  assert.match(html, /id="gdOrbit"/);
  assert.match(html, /js\/guardian-orbit\.js/);
  assert.match(html, /RCGuardianOrbit\.mount/);
  for (const href of ['/flight', '/stress', '/sentinel', '/intent', '/firewall', '/escape']) {
    assert.ok(html.includes(`href: '${href}'`), `orbit links ${href}`);
  }
  assert.match(html, /aria-label="The six Guardian modules/);
  const lib = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'guardian-orbit.js'), 'utf8');
  assert.match(lib, /prefers-reduced-motion/);
  assert.match(lib, /depth/);                        // it's a depth-sorted 3D scene
});
