/**
 * WEB-1: connect the LLM from the website — route + panel contract.
 *
 * The key's journey is web form -> Express (validate, never store) -> bot
 * gateway (encrypt at rest). These pins keep that contract from drifting:
 * the route is mounted and auth-gated, validation happens BEFORE the
 * gateway call, the ULTRA toggle exists (gateway re-checks admin), and the
 * Agent Hub actually renders the panel.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

test('server mounts /api/llm', () => {
  assert.match(read('server.js'), /app\.use\('\/api\/llm', require\('\.\/routes\/llm'\)\)/);
});

test('llm route is JWT-authed and rate-limited on writes', () => {
  const src = read('routes/llm.js');
  assert.match(src, /router\.use\(authMiddleware\)/);
  assert.match(src, /rateLimit\(/);
  assert.match(src, /router\.post\('\/', writeLimit/);
  assert.match(src, /router\.post\('\/ultra', writeLimit/);
});

test('provider and key are validated before any gateway call', () => {
  const src = read('routes/llm.js');
  const validate = src.indexOf("PROVIDERS.includes(provider)");
  const call = src.indexOf("postGateway('/llm'");
  assert.ok(validate > 0 && call > validate, 'validation must precede the proxy call');
  assert.match(src, /MAX_KEY_LEN = 512/);
  // Local/keyless providers are not offered from the web.
  assert.ok(!src.includes("'ollama'") && !src.includes("'runeclaw'"));
});

test('identity is resolved server-side, never from the request body', () => {
  const src = read('routes/llm.js');
  assert.match(src, /resolveBotIdentity\(req\)/);
  assert.ok(!src.includes('req.body.telegram_id'),
    'the browser must never choose whose key it sets');
});

test('express never persists the key itself', () => {
  const src = read('routes/llm.js');
  assert.ok(!/INSERT|pool\.execute|creds\.encrypt/i.test(src),
    'the key must pass through to the bot store, not be stored web-side');
});

test('agent hub renders the AI engine panel with connect + ultra hooks', () => {
  const src = read('public/js/dashboard.js');
  assert.ok(src.includes('p-hubllm') && src.includes('c-hubllm'));
  assert.ok(src.includes("fetchJSON('/api/llm'"));
  assert.ok(src.includes("'/api/llm/clear'") && src.includes("'/api/llm/ultra'"));
  // The key input never echoes a stored key back into the DOM.
  assert.match(src, /id="hubLlmKey" type="password"/);
});
