// Proof-of-PnL is the thesis artifact ("don't trust the dashboard — verify the
// fills"), but the IA audit found it was reachable from exactly one secondary
// button. Now that the publisher feed is live, it must be surfaced across the
// entry points. These assert the surfacing so it can't silently regress.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');

test('landing nav + footer link to /proof', () => {
  const html = pub('index.html');
  // Both the topbar nav and the footer should carry a /proof link.
  const proofLinks = html.match(/href="\/proof"/g) || [];
  assert.ok(proofLinks.length >= 2, `expected >=2 /proof links, found ${proofLinks.length}`);
  // Nav link is i18n-tagged like its siblings.
  assert.match(html, /href="\/proof"[^>]*data-i18n="nav\.proof"/);
});

test('nav.proof exists in all six offered languages', () => {
  const i18n = pub('js/i18n.js');
  const line = i18n.split('\n').find((l) => l.includes("'nav.proof'"));
  assert.ok(line, 'nav.proof key missing from i18n dictionary');
  for (const lang of ['en', 'es', 'zh', 'pt', 'fr', 'ar']) {
    assert.match(line, new RegExp(`${lang}:\\s*'[^']+'`), `nav.proof missing ${lang}`);
  }
});

test('dashboard Home has a verify-the-fills trust panel linking /proof and /track', () => {
  const js = pub('js/dashboard.js');
  assert.match(js, /id="p-verify"/, 'trust panel missing');
  assert.match(js, /href="\/proof"/, 'trust panel does not link /proof');
  assert.match(js, /href="\/track"/, 'trust panel does not link /track');
  assert.match(js, /verify the fills/i, 'trust panel missing the thesis line');
});
