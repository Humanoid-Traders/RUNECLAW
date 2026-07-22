'use strict';
/**
 * WEB-VISION discovery: the web chat surfaces the "read a chart" capability via
 * a chip that opens the image picker, and labels the wait while it reads.
 * Source-asserted (the chat module is browser-only DOM code).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const chat = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');

test('a discovery chip opens the image picker (logged-in only)', () => {
  assert.match(chat, /chat-chip--vision/);
  assert.match(chat, /Read a chart/);
  // Gated to logged-in users with the attach UI present.
  assert.match(chat, /if \(!PUBLIC && fileInput\)[\s\S]*fileInput\.click\(\)/);
});

test('vision turns show a "reading your screenshot" state', () => {
  assert.match(chat, /Reading your screenshot/);
  assert.match(chat, /imgs\.length \?/);
  assert.match(css, /\.chat-vision-label/);
});
