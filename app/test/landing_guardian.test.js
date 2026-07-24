'use strict';
/**
 * Landing page tells the whole story: a Guardian showcase section (the
 * differentiated safety suite) and hero "explore" links to the flagship
 * experiences, so a first-time visitor sees the breadth — not just a signup box.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the hero shows explore links to the flagship experiences', () => {
  const hero = index.slice(index.indexOf('class="hero"'), index.indexOf('</header>'));
  assert.match(hero, /class="hero-explore"/);
  assert.match(hero, /href="\/strengthmap"/);
  assert.match(hero, /href="\/guardian"/);
  assert.match(hero, /href="\/agents"/);
});

test('a Guardian section showcases every module, each linking to its live tool', () => {
  const sec = index.slice(index.indexOf('id="guardianTease"'));
  const cut = sec.slice(0, sec.indexOf('</section>') + 10);
  for (const href of ['/flight', '/stress', '/sentinel', '/firewall', '/escape', '/dashboard#trade']) {
    assert.ok(cut.includes(`href="${href}"`), `Guardian section links ${href}`);
  }
  assert.match(cut, /Flight Recorder/);
  assert.match(cut, /Universal Escape Agent/);
  assert.match(cut, /data-i18n="sec\.guardian_h"/);
  assert.match(cut, /data-i18n="sec\.guardian_cta"/);
  assert.match(cut, /href="\/guardian"/);              // "Explore Guardian" CTA
});

test('the new landing copy is translated (all six locales) + cache-buster bumped', () => {
  for (const key of ['sec.guardian_h', 'sec.guardian_p', 'sec.guardian_cta', 'hero.explore_map']) {
    assert.match(i18n, new RegExp("'" + key.replace('.', '\\.') + "'"));
  }
  // sec.guardian_p carries all six locale codes
  const line = i18n.split('\n').find(l => l.includes("'sec.guardian_p'"));
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(line.includes(loc), `guardian_p has ${loc}`);
  assert.match(index, /i18n\.js\?v=\d+/);
});
