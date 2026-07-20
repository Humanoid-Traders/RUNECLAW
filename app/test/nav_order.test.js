// The dashboard nav was in build-order: Trade (#10) and Portfolio (#11) sat past
// the midpoint while Agent Hub (MCP tools for external agents) was #3. These
// assert the journey-order fix so it can't silently regress, and that the mobile
// tabbar scrolls rather than crushing 15 items.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');

function viewOrder() {
  const js = read('js/dashboard.js');
  const block = js.slice(js.indexOf('const VIEWS = ['), js.indexOf('];', js.indexOf('const VIEWS = [')));
  return [...block.matchAll(/id:\s*'([a-z]+)'/g)].map((m) => m[1]);
}

test('core trading journey comes before advanced/analyst surfaces', () => {
  const order = viewOrder();
  const idx = (id) => order.indexOf(id);
  // The first-session loop is front-loaded.
  assert.ok(idx('chat') < idx('markets'), 'chat should precede markets');
  assert.ok(idx('signals') < idx('trade'), 'signals should precede trade');
  assert.ok(idx('trade') < idx('macro'), 'trade should precede advanced surfaces');
  assert.ok(idx('portfolio') < idx('macro'), 'portfolio should precede advanced surfaces');
  // Agent Hub (external-agent tooling) is demoted out of the top slots.
  assert.ok(idx('hub') > idx('portfolio'), 'Agent Hub should not sit above the core loop');
  assert.ok(idx('hub') >= 10, 'Agent Hub should live near the bottom');
  // Account stays last.
  assert.strictEqual(order[order.length - 1], 'account');
});

test('mobile tabbar scrolls horizontally instead of crushing tabs', () => {
  const css = read('styles.css');
  const bar = css.slice(css.indexOf('.tabbar {'), css.indexOf('}', css.indexOf('.tabbar {')));
  assert.match(bar, /overflow-x:\s*auto/, 'tabbar must scroll horizontally');
  const tab = css.slice(css.indexOf('.tabbar a {'), css.indexOf('}', css.indexOf('.tabbar a {')));
  assert.match(tab, /min-width:/, 'each tab needs a tappable min-width');
  assert.doesNotMatch(tab, /flex:\s*1;/, 'tabs must not use flex:1 (crushes 15 items)');
});
