'use strict';
/**
 * Intent Compiler (Guardian) — the deterministic NL→policy compiler behind the
 * public /intent demo. Asserts that plain-language limits compile to the right
 * typed rules and enforcement tiers, that it is §4-safe (no dollar figures ever
 * leave the compiler, even when the input names one), and that coverage +
 * warnings behave. Pure model, so every case is exact.
 */
const test = require('node:test');
const assert = require('node:assert');
const { compile, encodeShare, decodeShare, TIER, AXES } = require('../public/js/intent-model');

const ruleFor = (out, axis) => out.rules.find((r) => r.axis === axis);

test('empty input compiles to an empty, safe envelope', () => {
  const out = compile('');
  assert.equal(out.rules.length, 0);
  assert.equal(out.recognized, 0);
  assert.ok(/plain words/i.test(out.summary));
});

test('the flagship example compiles the expected rule set', () => {
  const out = compile('only majors, max 5% per trade, no shorts, min confidence 70%, stop if down 8%');
  assert.equal(ruleFor(out, 'size').value, 5);
  assert.equal(ruleFor(out, 'scope').label, 'Majors only');
  assert.equal(ruleFor(out, 'direction').value, 'long_only');
  assert.equal(ruleFor(out, 'gate_conf').value, 70);
  assert.equal(ruleFor(out, 'loss').value, 8);
  assert.ok(out.recognized >= 5);
});

test('size caps are wallet-enforced; direction is gate-checked', () => {
  const out = compile('max 10% per position, long only');
  assert.equal(ruleFor(out, 'size').tier, 'wallet');
  assert.equal(ruleFor(out, 'direction').tier, 'gate');
  assert.equal(ruleFor(out, 'size').tier_label, TIER.wallet.label);
});

test('“keep 30% in cash” compiles to a 70% max-exposure rule', () => {
  const out = compile('keep 30% in stables');
  assert.equal(ruleFor(out, 'exposure').value, 70);
});

test('no-leverage and a leverage cap are distinct wallet rules', () => {
  assert.equal(ruleFor(compile('no leverage'), 'leverage').value, 1);
  assert.equal(ruleFor(compile('max 3x leverage'), 'leverage').value, 3);
});

test('daily-loss phrasing is tagged as a daily stop', () => {
  const out = compile('stop for the day at 4%');
  const loss = ruleFor(out, 'loss');
  assert.equal(loss.value, 4);
  assert.match(loss.label, /Daily/);
});

test('§4: a dollar approval limit never leaks the figure', () => {
  const out = compile('trades over $5000 need my approval');
  const ap = ruleFor(out, 'approval');
  assert.ok(ap, 'an approval rule is compiled');
  assert.equal(ap.tier, 'approval');
  // The compiled output must contain no dollar figure anywhere.
  const blob = JSON.stringify(out);
  assert.ok(!/\$\s?\d/.test(blob), 'no $-amount in the compiled envelope');
  assert.ok(!/5000/.test(blob), 'the literal figure never appears');
});

test('§4: no dollar figure survives even a $-heavy prompt', () => {
  const out = compile('max 5% per trade, approve anything above $10,000, stop if down $2000');
  assert.ok(!/\$\s?\d/.test(JSON.stringify(out)));
});

test('percent values are clamped into 1..100', () => {
  const out = compile('max 500% per trade');
  assert.ok(ruleFor(out, 'size').value <= 100);
});

test('coverage counts core axes and lists what is missing', () => {
  const out = compile('max 5% per trade');
  assert.equal(out.coverage.total, AXES.length);
  assert.ok(out.coverage.axes.size);
  assert.ok(out.coverage.missing.includes('loss'));
});

test('missing a loss stop raises a warning', () => {
  const out = compile('max 5% per trade, no shorts');
  assert.ok(out.warnings.some((w) => /loss stop/i.test(w)));
});

test('an allow-list of specific symbols compiles', () => {
  const out = compile('only BTC and ETH');
  const scope = ruleFor(out, 'scope');
  assert.deepEqual(scope.value, ['BTC', 'ETH']);
});

test('an emergency trigger compiles to a monitored escape hand-off', () => {
  const out = compile('max 5% per trade, unwind everything on a depeg');
  const em = ruleFor(out, 'emergency');
  assert.ok(em);
  assert.equal(em.tier, 'monitor');
});

test('rules are ordered by enforcement rank (wallet before gate before approval)', () => {
  const out = compile('trades over $5000 need approval, no shorts, max 5% per trade');
  const ranks = out.rules.map((r) => TIER[r.tier].rank);
  const sorted = ranks.slice().sort((a, b) => a - b);
  assert.deepEqual(ranks, sorted);
});

test('the compiler is deterministic — same input, same output', () => {
  const a = JSON.stringify(compile('only majors, max 5% per trade, stop if down 8%'));
  const b = JSON.stringify(compile('only majors, max 5% per trade, stop if down 8%'));
  assert.equal(a, b);
});

test('a share link round-trips the policy text', () => {
  const text = 'only majors, max 5% per trade, no shorts, stop if down 8%';
  const round = decodeShare('#' + encodeShare(text));
  assert.equal(round, text);
  // and the decoded text compiles to the same envelope
  assert.deepEqual(compile(round).rules, compile(text).rules);
});

test('decodeShare tolerates a bare fragment, extra params and garbage', () => {
  assert.equal(decodeShare('#p=' + encodeURIComponent('long only')), 'long only');
  assert.equal(decodeShare('p=' + encodeURIComponent('no leverage')), 'no leverage');   // no leading '#'
  assert.equal(decodeShare('#a=1&p=' + encodeURIComponent('max 5% per trade')), 'max 5% per trade');
  assert.equal(decodeShare('#nothing-here'), '');
  assert.equal(decodeShare(''), '');
  assert.equal(decodeShare(null), '');
});

test('a share link special-chars survive the round-trip', () => {
  const text = 'keep 40% in stables & max 3x leverage';
  assert.equal(decodeShare('#' + encodeShare(text)), text);
});

test('each rule carries no secrets (§F-15)', () => {
  const blob = JSON.stringify(compile('only majors, max 5% per trade, stop if down 8%')).toLowerCase();
  for (const needle of ['secret', 'api_key', 'private key', 'password', 'llm_api', '.env']) {
    assert.ok(!blob.includes(needle), `envelope must not contain "${needle}"`);
  }
});
