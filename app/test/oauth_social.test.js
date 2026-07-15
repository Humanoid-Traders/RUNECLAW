'use strict';
/**
 * Social login/link expansion — redirect-based OAuth2 (Discord, X).
 *
 * Unit-tests the pure oauth2 helper (provider gating, PKCE, URL building,
 * response parsing), then drives the auth routes end-to-end against MemoryDB
 * with the network calls (token exchange + profile fetch) stubbed — proving the
 * login flow creates an account and the link flow attaches to a logged-in one.
 *
 * Run: npm test  (node --test test/)
 */

process.env.JWT_SECRET = 'j'.repeat(64);
delete process.env.DATABASE_URL;
process.env.APP_BASE_URL = 'https://app.test';
process.env.DISCORD_CLIENT_ID = 'disc-client';
process.env.DISCORD_CLIENT_SECRET = 'disc-secret';
delete process.env.X_CLIENT_ID; // leave X unconfigured to test gating

const test = require('node:test');
const assert = require('node:assert');
const express = require('express');

const oauth2 = require('../lib/oauth2');

// --- oauth2 unit ---

test('provider gating reflects configured env', () => {
  assert.strictEqual(oauth2.isProviderConfigured('discord'), true);
  assert.strictEqual(oauth2.isProviderConfigured('x'), false);
  assert.deepStrictEqual(oauth2.configuredProviders(), ['discord']);
  assert.strictEqual(oauth2.isProviderConfigured('nope'), false);
});

test('buildAuthorizeUrl adds PKCE only for providers that need it', () => {
  const d = oauth2.buildAuthorizeUrl('discord', {
    redirectUri: 'https://app.test/cb', state: 'st', challenge: 'ch',
  });
  assert.ok(d.startsWith('https://discord.com/oauth2/authorize?'));
  assert.ok(d.includes('client_id=disc-client'));
  assert.ok(!d.includes('code_challenge'), 'discord does not use PKCE');
  // X uses PKCE — temporarily configure it.
  process.env.X_CLIENT_ID = 'x-client';
  process.env.X_CLIENT_SECRET = 'x-secret';
  const x = oauth2.buildAuthorizeUrl('x', {
    redirectUri: 'https://app.test/cb', state: 'st', challenge: 'ch',
  });
  assert.ok(x.includes('code_challenge=ch'));
  assert.ok(x.includes('code_challenge_method=S256'));
  delete process.env.X_CLIENT_ID;
  delete process.env.X_CLIENT_SECRET;
});

test('pkcePair produces a verifier and its S256 challenge', () => {
  const { verifier, challenge } = oauth2.pkcePair();
  assert.ok(verifier.length >= 40);
  assert.ok(challenge.length >= 40);
  assert.notStrictEqual(verifier, challenge);
});

test('parseProfile normalizes discord and x shapes', () => {
  const d = oauth2.PROVIDERS.discord.parseProfile(
    { id: '123', email: 'a@b.com', verified: true, avatar: 'av' });
  assert.strictEqual(d.providerId, '123');
  assert.strictEqual(d.email, 'a@b.com');
  const dUnverified = oauth2.PROVIDERS.discord.parseProfile(
    { id: '123', email: 'a@b.com', verified: false });
  assert.strictEqual(dUnverified.email, null, 'unverified discord email is not trusted');
  const x = oauth2.PROVIDERS.x.parseProfile({ data: { id: '999', username: 'z' } });
  assert.strictEqual(x.providerId, '999');
  assert.strictEqual(x.email, null);
});

// --- auth route integration ---

// Stub the two network calls so the callback runs offline.
oauth2.exchangeCode = async () => 'stub-access-token';
let nextProfile = { providerId: 'disc-1', email: 'social@example.com', avatarUrl: null };
oauth2.fetchProfile = async () => nextProfile;

const { router: authRouter } = require('../auth');

let server;
let base;

test.before(async () => {
  const app = express();
  app.use(express.json());
  app.use('/api/auth', authRouter);
  await new Promise((resolve) => {
    server = app.listen(0, '127.0.0.1', () => {
      base = `http://127.0.0.1:${server.address().port}`;
      resolve();
    });
  });
});
test.after(() => { if (server) server.close(); });

async function startAndGetState(provider, linkQuery) {
  const url = base + `/api/auth/oauth/${provider}/start` + (linkQuery ? `?link=${linkQuery}` : '');
  const r = await fetch(url, { redirect: 'manual' });
  assert.strictEqual(r.status, 302);
  const loc = r.headers.get('location');
  return new URL(loc).searchParams.get('state');
}

test('config advertises configured providers only', async () => {
  const cfg = await fetch(base + '/api/auth/config').then((r) => r.json());
  assert.deepStrictEqual(cfg.oauth_providers, ['discord']);
  assert.ok(cfg.social_links);
});

test('start 503s for an unconfigured provider', async () => {
  const r = await fetch(base + '/api/auth/oauth/x/start', { redirect: 'manual' });
  assert.strictEqual(r.status, 503);
});

test('login flow: callback creates a session for a new social identity', async () => {
  nextProfile = { providerId: 'disc-new', email: 'newsocial@example.com', avatarUrl: null };
  const state = await startAndGetState('discord');
  const r = await fetch(
    base + `/api/auth/oauth/discord/callback?code=abc&state=${state}`, { redirect: 'manual' });
  assert.strictEqual(r.status, 302);
  const loc = r.headers.get('location');
  assert.ok(loc.startsWith('/#oauth='), loc);
  const payload = JSON.parse(Buffer.from(loc.split('#oauth=')[1], 'base64').toString());
  assert.ok(payload.token);
  assert.strictEqual(payload.email, 'newsocial@example.com');
  assert.strictEqual(payload.provider, 'discord');
  assert.strictEqual(payload.linked, false);
});

test('callback rejects an unknown/expired state', async () => {
  const r = await fetch(
    base + '/api/auth/oauth/discord/callback?code=abc&state=bogus', { redirect: 'manual' });
  assert.strictEqual(r.status, 302);
  assert.ok(r.headers.get('location').startsWith('/#oauth_error='));
});

test('link flow: a logged-in user attaches the provider to their account', async () => {
  // Register an email account, get its token.
  const reg = await fetch(base + '/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email: 'linker@example.com', password: 'longenough1' }),
  }).then((r) => r.json());
  // Mint a link key.
  const lk = await fetch(base + '/api/auth/oauth-link-token', {
    method: 'POST', headers: { Authorization: `Bearer ${reg.token}`, 'Content-Type': 'application/json' },
    body: '{}',
  }).then((r) => r.json());
  assert.ok(lk.link_key);
  // Drive start with the link key, then callback.
  nextProfile = { providerId: 'disc-link-42', email: 'different@example.com', avatarUrl: null };
  const state = await startAndGetState('discord', lk.link_key);
  const r = await fetch(
    base + `/api/auth/oauth/discord/callback?code=abc&state=${state}`, { redirect: 'manual' });
  const payload = JSON.parse(Buffer.from(
    r.headers.get('location').split('#oauth=')[1], 'base64').toString());
  assert.strictEqual(payload.linked, true);
  assert.strictEqual(payload.user_id, reg.user_id, 'linked the SAME account, no new user');
  assert.strictEqual(payload.email, 'linker@example.com', 'keeps the original email');
});
