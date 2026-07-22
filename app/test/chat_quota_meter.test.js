'use strict';
/**
 * Free-tier chat meter — surfaces the Grok question quota in the web chat.
 *
 * The backend already returns a `quota` object ({exempt, limit, used, remaining})
 * on every chat answer and gates free users at N/day. This closes the loop on the
 * UI: after each answer a free (non-exempt) user sees how many free questions
 * remain, with an upgrade CTA on the last one. Browser-only DOM, so source-
 * asserted: the meter reads r.data.quota, shows only for a real chat answer to a
 * non-exempt user, renders the remaining/limit count, and links to plans when the
 * allowance is spent.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const chat = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'chat.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('the chat reads the quota from the response and gates the meter to free users', () => {
  assert.match(chat, /r\.data\.quota/);
  // only a real LLM answer (intent 'chat') to a NON-exempt user shows the meter.
  assert.match(chat, /intent === 'chat' && _q && _q\.exempt === false/);
  assert.match(chat, /typeof _q\.remaining === 'number'/);
});

test('the meter shows the remaining count and an upgrade CTA on the last question', () => {
  assert.match(chat, /free questions left today/);
  assert.match(chat, /_q\.remaining > 0/);
  // last free question → upgrade link deep-linked to the Membership panel.
  assert.match(chat, /upgrade for unlimited/);
  assert.match(chat, /href="\/dashboard#account\/aplan"/);
});

test('the Membership plans answer the chat cap the meter surfaces', () => {
  const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
  // Basic names the daily cap; Pro sells unlimited — the value prop the free
  // user hit is reflected right where the upgrade CTA lands.
  assert.match(dash, /AI chat \(5 questions\/day\)/);
  assert.match(dash, /Unlimited AI chat/);
});

test('the meter is dormant when the cap is off (no quota field on the response)', () => {
  // when the free-chat cap is dormant (no funded Grok), the gateway omits quota;
  // guarding on `_q &&` means nothing renders — no false "0 left" on free chat.
  assert.match(chat, /const _q = r\.data\.quota;/);
});

test('the chat.js cache-buster is bumped on both surfaces', () => {
  assert.match(html, /chat\.js\?v=2\d/);
  assert.match(dash, /chat\.js\?v=2\d/);
});
