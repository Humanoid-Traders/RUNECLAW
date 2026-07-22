'use strict';
/**
 * I18N-1 — public (anonymous) chat answers in the visitor's UI language.
 *
 * A signed-in user's reply language is resolved server-side from their stored
 * profile, but an anonymous visitor has no profile — so the ONLY way the public
 * assistant can answer in the language the visitor picked is for the site to
 * send the current locale. Source-asserted: chat.js forwards the RCI18N locale
 * on the public path (and only there), and the Express route validates it to a
 * short language token before relaying it to the account-free gateway endpoint.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const chat = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
const route = fs.readFileSync(path.join(__dirname, '..', 'routes', 'public_chat.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the client forwards the current UI locale on the public chat path only', () => {
  // reads the active language from the i18n module…
  assert.match(chat, /window\.RCI18N && window\.RCI18N\.getLang/);
  // …and attaches it to the request body, gated to anonymous (PUBLIC) visitors,
  // skipping the default 'en' so nothing changes for English.
  assert.match(chat, /if \(PUBLIC\) \{[\s\S]*body\.lang = lang/);
  assert.match(chat, /lang !== 'en'/);
});

test('the public route validates the lang token and forwards it to the gateway', () => {
  // a short, charset-restricted language token — never arbitrary text.
  assert.match(route, /\/\^\[a-zA-Z-\]\{2,12\}\$\/\.test\(rawLang\)/);
  // forwarded to the SAME account-free endpoint; the payload is text (+lang) only.
  assert.match(route, /postGateway\('\/chat\/public', payload/);
  assert.match(route, /const payload = lang \? \{ text, lang \} : \{ text \}/);
  // no identity resolution ever happens on the public path.
  assert.ok(!/telegram_id|resolveBotIdentity|authMiddleware/.test(route),
    'still no identity on the public path');
});

test('the chat.js cache-buster is bumped on both surfaces', () => {
  assert.match(html, /chat\.js\?v=2\d/);
  assert.match(dash, /chat\.js\?v=2\d/);
});
