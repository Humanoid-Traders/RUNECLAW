'use strict';
/**
 * Contract Studio web view — the dashboard surface for AI Solidity drafting.
 *
 * Source-asserted: the view is registered in the nav + router, posts the spec to
 * /api/contract/studio, renders the draft as TEXT (never interpreted as HTML),
 * shows the security flags with the audit disclaimer, and the cache-buster is
 * bumped. It is a DRAFT-with-FLAGS surface — never claims audited/safe, and
 * carries no money-path.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'dashboard.html'), 'utf8');

test('Contract Studio is registered in the nav and the view router', () => {
  assert.match(dash, /id: 'studio',\s*label: 'Contract Studio'/);
  assert.match(dash, /studio: renderContractStudio/);
  assert.match(dash, /function renderContractStudio\(/);
});

test('the view posts the spec to the studio API', () => {
  assert.match(dash, /fetchJSON\('\/api\/contract\/studio',\s*\n?\s*\{ method: 'POST'/);
  assert.match(dash, /body: \{ spec, license, pragma \}/);
});

test('it renders the draft as text and shows flags + disclaimer', () => {
  // textContent — the Solidity is never interpreted as HTML.
  assert.match(dash, /getElementById\('cs-code'\)\.textContent = d\.solidity/);
  assert.match(dash, /d\.flags/);
  assert.match(dash, /d\.disclaimer/);
  // clean ≠ safe — the empty state still says "not a safety guarantee".
  assert.match(dash, /not<\/b> a safety guarantee/);
});

test('the studio view carries no money-path', () => {
  const fn = dash.slice(dash.indexOf('function renderContractStudio('));
  const body = fn.slice(0, fn.indexOf('async function renderLeaderboard('));
  assert.ok(!/signTransaction|sendRawTransaction|private_key|broadcast|value_wei/.test(body),
    'drafting only — no signing or value movement in the studio view');
});

test('one-tap template chips pre-fill the spec', () => {
  // starters lower the blank-page barrier; clicking a chip sets the textarea.
  assert.match(dash, /id="cs-templates"/);
  assert.match(dash, /data-tpl="an ERC-20 token/);
  assert.match(dash, /data-tpl="an ERC-721 NFT/);
  assert.match(dash, /button\[data-tpl\]/);
  assert.match(dash, /ta\.value = t\.getAttribute\('data-tpl'\)/);
});

test('the draft panel lets you take the code out (copy + download .sol)', () => {
  // a codegen tool has to let you keep the result.
  assert.match(dash, /id="cs-copy"/);
  assert.match(dash, /id="cs-download"/);
  assert.match(dash, /navigator\.clipboard\.writeText/);
  // download builds a Contract.sol blob from the rendered text, not from HTML.
  assert.match(dash, /a\.download = 'Contract\.sol'/);
  assert.match(dash, /new Blob\(\[code\]/);
});

test('the dashboard.js cache-buster is bumped', () => {
  assert.match(html, /dashboard\.js\?v=\d\d+/);
});
