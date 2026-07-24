'use strict';
/**
 * Android path — the site is an installable PWA and TWA-ready: a rich
 * manifest (id, shortcuts, categories), a Digital Asset Links route that is
 * env-gated (404 when unconfigured — honest, never an empty statement), the
 * key public pages linking the manifest so install prompts work everywhere,
 * and mobile-first touch targets. docs/ANDROID.md documents the Bubblewrap
 * wrap for the operator.
 */
process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');

test('manifest is TWA-rich: id, shortcuts, categories, portrait, maskable icon', () => {
  const m = JSON.parse(pub('manifest.json'));
  assert.equal(m.id, '/dashboard');
  assert.equal(m.display, 'standalone');
  assert.equal(m.orientation, 'portrait');
  assert.ok(m.categories.includes('finance'));
  assert.ok(m.shortcuts.length >= 4);
  const urls = m.shortcuts.map((s) => s.url);
  for (const u of ['/dashboard', '/arena', '/guardian']) assert.ok(urls.includes(u), `shortcut ${u}`);
  assert.ok(m.icons.some((i) => /maskable/.test(i.purpose || '')));
});

test('assetlinks route is wired and env-gated', () => {
  const srv = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  assert.match(srv, /\/\.well-known\/assetlinks\.json/);
  assert.match(srv, /ANDROID_PACKAGE/);
  assert.match(srv, /ANDROID_CERT_SHA256/);
  assert.match(srv, /status\(404\)/);
  assert.match(srv, /delegate_permission\/common\.handle_all_urls/);
});

test('key public pages link the manifest so install prompts work everywhere', () => {
  for (const f of ['index.html', 'dashboard.html', 'arena.html', 'guardian.html', 'leaderboard.html', 'strengthmap.html']) {
    assert.match(pub(f), /rel="manifest"/, `${f} links manifest`);
    assert.match(pub(f), /apple-touch-icon/, `${f} has touch icon`);
  }
});

test('mobile touch targets ship in the stylesheet', () => {
  const css = pub('styles.css');
  assert.match(css, /pointer: coarse/);
  assert.match(css, /min-height: 40px/);
});

test('the operator guide exists and covers Bubblewrap + assetlinks', () => {
  const md = fs.readFileSync(path.join(__dirname, '..', '..', 'docs', 'ANDROID.md'), 'utf8');
  assert.match(md, /bubblewrap init/);
  assert.match(md, /assetlinks\.json/);
  assert.match(md, /ANDROID_PACKAGE/);
  assert.match(md, /BACK IT UP/);
});
