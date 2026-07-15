'use strict';
/**
 * UX polish batch — web-chat suggestion chips + animated typing indicator, plus
 * the shared badge-contrast / micro-interaction CSS.
 *
 * chat.js is a browser IIFE that early-returns when its DOM nodes are absent, so
 * we load it in a vm with a stub DOM to prove it parses and runs cleanly, then
 * assert the polish hooks are wired (source) and the CSS rules exist.
 */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const CHAT = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
const CSS = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('chat.js loads without throwing when its DOM is absent', () => {
  const sandbox = {
    window: { RC: { LOGGED_IN: false, fetchJSON() {}, esc: (s) => s, fmt() {},
      sanitizeBotHtml: (s) => s, toast() {}, modalA11y: () => ({ open() {}, close() {} }) },
      addEventListener() {} },
    document: { getElementById() { return null; }, addEventListener() {},
      createElement() { return {}; } },
    JSON, console,
  };
  assert.doesNotThrow(() => vm.runInNewContext(CHAT, sandbox));
});

test('chat wires suggestion chips + animated typing dots', () => {
  assert.match(CHAT, /CHIP_PROMPTS\s*=/, 'suggestion prompts defined');
  assert.match(CHAT, /function renderChips/, 'chips renderer present');
  assert.match(CHAT, /function hideChips/, 'chips hide on send/conversation');
  // The pending bubble is the animated three-dot indicator, not static text.
  assert.match(CHAT, /typing-dots/, 'typing indicator markup');
  assert.ok(!/appendMsg\('bot', 'Thinking…'/.test(CHAT), 'static "Thinking…" replaced');
});

test('CSS ships the typing/chip/badge/micro-interaction rules', () => {
  assert.match(CSS, /\.typing-dots span\s*{[^}]*animation:\s*typing-bounce/,
    'typing dots animate');
  assert.match(CSS, /@keyframes typing-bounce/, 'typing keyframes');
  assert.match(CSS, /\.chat-chip/, 'chat chip style');
  assert.match(CSS, /\.chat-chips:empty\s*{\s*display:\s*none/, 'empty chip row collapses');
  // Paper badge now uses a solid fill (dark text) — no longer amber-on-dim-amber.
  assert.match(CSS, /\.mode-badge--paper\s*{[^}]*background:\s*var\(--warn\)/,
    'paper badge solid fill for contrast');
  assert.match(CSS, /\.btn:active:not\(:disabled\)/, 'button press micro-interaction');
});
