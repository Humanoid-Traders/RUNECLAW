'use strict';
/**
 * Web i18n engine (public/js/i18n.js) — pure-logic + dictionary-integrity tests.
 * Runs the module under Node (dual-mode export); the DOM apply/switcher paths
 * are browser-only and not exercised here.
 */
const test = require('node:test');
const assert = require('node:assert');
const i18n = require('../public/js/i18n');

test('normalize strips region subtags and lowercases', () => {
  assert.equal(i18n.normalize('pt-BR'), 'pt');
  assert.equal(i18n.normalize('ZH_TW'), 'zh');
  assert.equal(i18n.normalize('EN'), 'en');
  assert.equal(i18n.normalize(''), '');
  assert.equal(i18n.normalize(null), '');
});

test('resolveLang: saved choice > browser > English', () => {
  assert.equal(i18n.resolveLang('es', 'fr-FR'), 'es');        // saved wins
  assert.equal(i18n.resolveLang(null, 'fr-FR'), 'fr');        // browser fallback
  assert.equal(i18n.resolveLang('xx', 'yy'), 'en');           // unknown -> en
  assert.equal(i18n.resolveLang(null, 'pt-BR'), 'pt');        // region normalized
});

test('translate returns the language string, falls back to English, null on miss', () => {
  assert.equal(i18n.translate('nav.dashboard', 'es'), 'Panel');
  assert.equal(i18n.translate('nav.dashboard', 'zz'), 'Dashboard');   // fallback en
  assert.equal(i18n.translate('does.not.exist', 'es'), null);
});

test('every dictionary key defines all offered languages (no silent gaps)', () => {
  const codes = i18n.LANGS.map((l) => l.code);
  const missing = [];
  for (const [key, entry] of Object.entries(i18n.STRINGS)) {
    for (const c of codes) {
      if (typeof entry[c] !== 'string' || !entry[c].length) missing.push(`${key}:${c}`);
    }
  }
  assert.deepEqual(missing, [], 'missing translations: ' + missing.join(', '));
});

test('every offered language has a non-empty display name', () => {
  for (const l of i18n.LANGS) {
    assert.ok(l.code && l.name, `lang ${JSON.stringify(l)} needs code+name`);
  }
});
