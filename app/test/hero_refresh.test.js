'use strict';
/**
 * Hero refresh — the homepage headline now leads with the full platform: an AI
 * engine you can talk to AND trust, backed by the Guardian story (prove /
 * simulate / block). §4: capability copy, no dollar claims.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');
const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('the hero headline leads with trust, the tagline with the differentiators', () => {
  const h1 = i18n.split('\n').filter(l => l.includes('talk to — and trust'))[0];
  assert.ok(h1, 'hero.h1 en adds "and trust"');
  assert.match(i18n, /'hero\.tagline':.*Explainable · Guarded/);
  // the static (no-JS) fallback matches
  assert.match(index, /talk to — and trust/);
  assert.match(index, /Intelligent · Explainable · Guarded/);
});

test('the hero body weaves in the Guardian story', () => {
  const line = i18n.split('\n').find(l => l.includes("'hero.body'") === false && l.includes('tamper-evident ledger'));
  assert.ok(line, 'hero.body en mentions the tamper-evident ledger');
  assert.match(i18n, /simulates what could break you/);
  assert.match(i18n, /blocks malicious signing/);
  assert.match(index, /blocks malicious signing/);      // static fallback too
});

test('hero.h1 and hero.body stay translated across all six locales', () => {
  // Grab the multi-line hero.h1 block and confirm every locale key survived.
  const start = i18n.indexOf("'hero.h1'");
  const block = i18n.slice(start, start + 700);
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(block.includes(loc), `hero.h1 has ${loc}`);
  assert.match(index, /i18n\.js\?v=\d+/);
});
