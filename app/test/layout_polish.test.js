// IA audit #6 (promote the onboarding checklist to the top of Home) and #8
// (the "One loop" landing section was a heading with no body). Lock both.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');

test('Getting-started checklist is promoted above Open positions on Home', () => {
  const js = read('js/dashboard.js');
  const home = js.slice(js.indexOf('async function renderHome()'));
  const stack = home.slice(home.indexOf("class=\"stack\""), home.indexOf('renderPanel(C(\'hero\')'));
  const next = stack.indexOf('id="p-next"');
  const hpos = stack.indexOf('id="p-hpos"');
  const hero = stack.indexOf('id="p-hero"');
  assert.ok(next > -1 && hpos > -1, 'both panels must exist');
  assert.ok(next < hpos, 'the checklist must come before Open positions');
  assert.ok(next > hero && next - hero < 400, 'checklist sits right after the hero panel');
  // Exactly one checklist panel (not duplicated by the move).
  assert.strictEqual((stack.match(/id="p-next"/g) || []).length, 1);
});

test('the landing "One loop" section has real content, not an empty heading', () => {
  const html = read('index.html');
  const sec = html.slice(html.indexOf('id="how-h"'), html.indexOf('</section>', html.indexOf('id="how-h"')));
  const steps = sec.match(/loop-step/g) || [];
  assert.ok(steps.length >= 4, `expected the 4 loop steps, found ${steps.length}`);
  for (const label of ['Scan', 'Analyze', 'Risk gate', 'Execute']) {
    assert.ok(sec.includes(label), `loop step "${label}" missing`);
  }
});
