/**
 * UX-5: web chat polish (audit-confirmed). Six wired-up fixes in chat.js
 * (plus the fetchJSON cancel signal in app.js), verified by source assertion —
 * the chat module is a DOM/Speech-API surface with no headless harness.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const chat = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('restored history renders a timestamp from the gateway', () => {
  assert.match(chat, /function fmtChatTime/);
  assert.match(chat, /appendMsg\('user', m\.content, '', m\.timestamp\)/);
  assert.match(chat, /appendMsg\('bot', sanitizeBotHtml\(m\.content\), '', m\.timestamp\)/);
  assert.match(css, /\.chat-ts \{/);
});

test('mic failures are explained, not swallowed', () => {
  assert.match(chat, /recog\.onerror = \(ev\) =>/);
  assert.match(chat, /Microphone blocked/);
  assert.match(chat, /err !== 'no-speech' && err !== 'aborted'/);
});

test('rate-limit Retry has a real cooldown, not an instant re-fail', () => {
  assert.match(chat, /function appendFailure\(html, text, cooldownMs\)/);
  assert.match(chat, /Retry in \$\{left\}s/);
  assert.match(chat, /appendFailure\('Rate limit hit[^']*', text, 5000\)/);
});

test('messages sent mid-turn queue (one slot) instead of vanishing', () => {
  assert.match(chat, /let pending = null;/);
  assert.match(chat, /if \(pending == null\)/);
  assert.match(chat, /if \(pending != null\) \{ const t = pending; pending = null; send\(t\); \}/);
});

test('the typing indicator offers Cancel wired to an AbortController', () => {
  assert.match(chat, /const ac = new AbortController\(\)/);
  assert.match(chat, /cancelled = true; ac\.abort\(\)/);
  assert.match(chat, /signal: ac\.signal/);
  assert.match(chat, /if \(cancelled\) \{ if \(!input\.value\.trim\(\)\) input\.value = text; \}/);
  // fetchJSON honours an external caller signal.
  assert.match(app, /if \(signal\) \{/);
  assert.match(app, /signal\.addEventListener\('abort'/);
});

test('answers reveal progressively, instant for markup / reduced-motion', () => {
  assert.match(chat, /function revealInto\(div, html\)/);
  assert.match(chat, /prefers-reduced-motion: reduce/);
  assert.match(chat, /revealInto\(bubble, safeHtml\)/);
  assert.match(chat, /requestAnimationFrame\(step\)/);
});
