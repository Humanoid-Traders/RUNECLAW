'use strict';
/**
 * Landing "Why RUNECLAW is different" band — the trust pillars (explainable,
 * guarded, non-custodial, verifiable) that back the hero's "and trust" promise.
 * Capability claims only (§4).
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const index = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const i18n = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'i18n.js'), 'utf8');

test('the differentiator band names the four trust pillars', () => {
  const sec = index.slice(index.indexOf('id="whyTease"'));
  const cut = sec.slice(0, sec.indexOf('</section>') + 10);
  assert.match(cut, /data-i18n="sec\.why_h"/);
  assert.match(cut, /Explainable, not a black box/);
  assert.match(cut, /Guarded, not just optimized/);
  assert.match(cut, /Non-custodial by design/);
  assert.match(cut, /Verifiable, not claimed/);
  // it appears high on the page — before the marketplace tease
  assert.ok(index.indexOf('id="whyTease"') < index.indexOf('id="marketplaceTease"'));
});

test('the band copy is translated + cache-buster bumped', () => {
  assert.match(i18n, /'sec\.why_h'/);
  const line = i18n.split('\n').find(l => l.includes("'sec.why_p'"));
  for (const loc of ['en:', 'es:', 'zh:', 'pt:', 'fr:', 'ar:']) assert.ok(line.includes(loc), `why_p has ${loc}`);
  assert.match(index, /i18n\.js\?v=\d+/);
});
