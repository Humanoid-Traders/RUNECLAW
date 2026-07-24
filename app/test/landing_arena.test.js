'use strict';
/**
 * Landing promotion for the Paper Trading Arena — the growth on-ramp gets a
 * hero explore link and its own section band, fully translated. §4: the band
 * sells the mechanism (same stake, percent ranking, anonymous handles) and
 * shows no dollar figures on this public surface.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the hero explore row links the Arena', () => {
  const hero = index.slice(index.indexOf('class="hero"'), index.indexOf('</header>'));
  assert.match(hero, /href="\/arena"/);
  assert.match(hero, /data-i18n="hero\.explore_arena"/);
});

test('an Arena section band sells the mechanism and links the tool', () => {
  const sec = index.slice(index.indexOf('id="arenaTease"'));
  const cut = sec.slice(0, sec.indexOf('</section>') + 10);
  assert.match(cut, /data-i18n="sec\.arena_h"/);
  assert.match(cut, /data-i18n="sec\.arena_p"/);
  assert.match(cut, /data-i18n="sec\.arena_cta"/);
  assert.match(cut, /href="\/arena"/);
  // three mechanism cards, each translated
  for (const k of ['f1h', 'f1p', 'f2h', 'f2p', 'f3h', 'f3p']) {
    assert.ok(cut.includes(`data-i18n="sec.arena_${k}"`), `card key sec.arena_${k}`);
  }
  // §4: the public band shows no dollar amounts
  assert.ok(!/\$\s?\d/.test(cut), 'no $-amount in the arena band');
});

test('every arena key ships all six locales + cache-buster bumped', () => {
  for (const key of ['hero.explore_arena', 'sec.arena_h', 'sec.arena_p', 'sec.arena_cta',
    'sec.arena_f1h', 'sec.arena_f1p', 'sec.arena_f2h', 'sec.arena_f2p', 'sec.arena_f3h', 'sec.arena_f3p']) {
    const line = i18n.split('\n').find(l => l.includes(`'${key}'`));
    assert.ok(line, `i18n has ${key}`);
    for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) {
      assert.ok(line.includes(loc), `${key} has ${loc}`);
    }
  }
  const m = index.match(/i18n\.js\?v=(\d+)/);
  assert.ok(m && Number(m[1]) >= 12, 'i18n cache-buster >= 12');
});
