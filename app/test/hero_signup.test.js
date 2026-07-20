// The IA audit found the register form buried at landing position #7. The hero
// now carries an email-capture above the fold that hands off to the real form
// (no auth-logic duplication). These lock the surfacing + the safe hand-off.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');

test('hero has an email-capture form above the fold', () => {
  const html = read('index.html');
  const hero = html.slice(html.indexOf('<header class="hero">'), html.indexOf('</header>'));
  assert.match(hero, /id="heroSignup"/, 'hero signup form missing');
  const input = hero.match(/<input[^>]*id="hero-email"[^>]*>/);
  assert.ok(input, 'hero email input missing');
  assert.match(input[0], /type="email"/, 'hero email input must be type=email');
});

test('hero hand-off reuses the real register form, not a duplicate auth path', () => {
  const html = read('index.html');
  // It prefills the real form field and defers to the existing register flow.
  assert.match(html, /getElementById\('reg-email'\)/, 'must prefill the real reg-email');
  assert.match(html, /switchTab\('register'\)/, 'must switch to the register tab');
  // It must NOT re-implement the account-creation POST itself.
  const script = html.slice(html.indexOf('id="heroSignup"'));
  const handler = script.slice(0, script.indexOf('Landing mind-stream'));
  assert.doesNotMatch(handler, /fetch\(/, 'hero handler must not POST directly');
});

test('hero.free_note exists in all six languages', () => {
  const i18n = read('js/i18n.js');
  const line = i18n.split('\n').find((l) => l.includes("'hero.free_note'"));
  assert.ok(line, 'hero.free_note missing');
  for (const lang of ['en', 'es', 'zh', 'pt', 'fr', 'ar']) {
    assert.match(line, new RegExp(`${lang}:\\s*'`), `hero.free_note missing ${lang}`);
  }
});
