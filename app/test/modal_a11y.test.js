'use strict';
/**
 * RC.modalA11y — focus-trap + inert background + focus-return (audit P1, WCAG
 * 2.4.3 / 4.1.2). app.js is a browser IIFE, so we load it in a vm sandbox with
 * a minimal DOM stub and exercise the real open()/close() logic: aria-modal on
 * the dialog, `inert` on every OTHER top-level element (never the dialog or a
 * <script>), and focus returned to whatever was focused when it opened.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function fakeEl(tag) {
  return {
    tagName: tag,
    _a: {},
    setAttribute(k, v) { this._a[k] = v; },
    removeAttribute(k) { delete this._a[k]; },
    hasAttribute(k) { return Object.prototype.hasOwnProperty.call(this._a, k); },
    getClientRects() { return [{}]; },
    contains(x) { return x === this; },
    querySelectorAll() { return []; },
    disabled: false,
  };
}

function loadRC() {
  const src = fs.readFileSync(
    path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
  const sandbox = {
    window: {},
    document: {
      activeElement: null,
      body: { children: [] },
      addEventListener() {}, removeEventListener() {},
      getElementById() { return null; },
      createElement() { return fakeEl('DIV'); },
    },
    localStorage: { getItem() { return null; } },
    JSON, console,
  };
  sandbox.window.addEventListener = () => {};
  vm.runInNewContext(src, sandbox);
  return sandbox;
}

test('open() sets aria-modal + inerts siblings; close() restores + returns focus', () => {
  const s = loadRC();
  const RC = s.window.RC;
  assert.equal(typeof RC.modalA11y, 'function', 'modalA11y exported');

  const shell = fakeEl('DIV');
  const tabbar = fakeEl('NAV');
  const script = fakeEl('SCRIPT');
  const dialog = fakeEl('SECTION');
  s.document.body.children = [shell, tabbar, dialog, script];

  const trigger = fakeEl('BUTTON');
  trigger.focus = function () { s.document.activeElement = this; };
  s.document.activeElement = trigger;              // what had focus at open time

  const a11y = RC.modalA11y(dialog);
  a11y.open();

  assert.equal(dialog._a['aria-modal'], 'true', 'dialog is aria-modal');
  assert.ok(shell.hasAttribute('inert'), 'sibling inert');
  assert.ok(tabbar.hasAttribute('inert'), 'sibling inert');
  assert.ok(!dialog.hasAttribute('inert'), 'dialog itself NOT inert');
  assert.ok(!script.hasAttribute('inert'), '<script> skipped');

  a11y.close();

  assert.ok(!dialog.hasAttribute('aria-modal'), 'aria-modal cleared');
  assert.ok(!shell.hasAttribute('inert'), 'inert released');
  assert.ok(!tabbar.hasAttribute('inert'), 'inert released');
  assert.equal(s.document.activeElement, trigger, 'focus returned to trigger');
});

test('open() focuses the requested element', () => {
  const s = loadRC();
  const dialog = fakeEl('SECTION');
  s.document.body.children = [dialog];
  const input = fakeEl('INPUT');
  let focused = null;
  input.focus = function () { focused = this; };
  const a11y = s.window.RC.modalA11y(dialog);
  a11y.open(input);
  assert.equal(focused, input, 'explicit focus target honored');
  a11y.close();
});
