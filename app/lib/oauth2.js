'use strict';

// Redirect-based OAuth2 (authorization-code) for providers that don't offer a
// client-side token widget — currently Discord and X (Twitter). Google and
// Telegram keep their existing client-side flows in auth.js; this module is the
// server side of the "click → provider → back to us" round-trip.
//
// Design goals:
//   - Provider-agnostic: add a provider by adding one entry to PROVIDERS.
//   - Gated: each provider is "available" only when BOTH its client id and
//     secret env vars are set. Unconfigured providers are simply not offered.
//   - Testable: the network calls (token exchange, profile fetch) take an
//     injectable fetch, so the flow can be unit-tested without a live provider.
//
// The HTTP round-trips themselves live in auth.js routes; the pure pieces
// (provider config, PKCE, state, URL building, response parsing) are here.

const crypto = require('crypto');

function _b64url(buf) {
  return Buffer.from(buf).toString('base64')
    .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// PKCE (RFC 7636): verifier is a high-entropy random string; challenge is its
// SHA-256, base64url-encoded. Required by X; harmless for Discord (unused).
function pkcePair() {
  const verifier = _b64url(crypto.randomBytes(32));
  const challenge = _b64url(crypto.createHash('sha256').update(verifier).digest());
  return { verifier, challenge };
}

function randomState() {
  return _b64url(crypto.randomBytes(24));
}

const PROVIDERS = {
  discord: {
    idColumn: 'discord_id',
    authorizeUrl: 'https://discord.com/oauth2/authorize',
    tokenUrl: 'https://discord.com/api/oauth2/token',
    profileUrl: 'https://discord.com/api/users/@me',
    scope: 'identify email',
    usesPKCE: false,
    // Confidential client: token exchange authenticates with client_secret in
    // the POST body.
    clientIdEnv: 'DISCORD_CLIENT_ID',
    clientSecretEnv: 'DISCORD_CLIENT_SECRET',
    parseProfile(j) {
      // Discord returns a verified flag; only trust the email when verified.
      const email = j && j.verified && j.email
        ? String(j.email).trim().toLowerCase() : null;
      const avatar = j && j.id && j.avatar
        ? `https://cdn.discordapp.com/avatars/${j.id}/${j.avatar}.png` : null;
      return { providerId: String(j.id), email, avatarUrl: avatar };
    },
  },
  x: {
    idColumn: 'x_id',
    authorizeUrl: 'https://twitter.com/i/oauth2/authorize',
    tokenUrl: 'https://api.twitter.com/2/oauth2/token',
    profileUrl: 'https://api.twitter.com/2/users/me',
    scope: 'tweet.read users.read',
    usesPKCE: true,
    clientIdEnv: 'X_CLIENT_ID',
    clientSecretEnv: 'X_CLIENT_SECRET',
    parseProfile(j) {
      // X never returns an email; the caller synthesizes a placeholder.
      const d = (j && j.data) || {};
      return { providerId: String(d.id), email: null, avatarUrl: d.profile_image_url || null };
    },
  },
};

function providerConfig(name) {
  return PROVIDERS[name] || null;
}

function isProviderConfigured(name) {
  const p = PROVIDERS[name];
  if (!p) return false;
  return Boolean(process.env[p.clientIdEnv] && process.env[p.clientSecretEnv]);
}

function configuredProviders() {
  return Object.keys(PROVIDERS).filter(isProviderConfigured);
}

// Build the provider's authorization URL the browser is redirected to.
function buildAuthorizeUrl(name, { redirectUri, state, challenge }) {
  const p = PROVIDERS[name];
  const clientId = process.env[p.clientIdEnv];
  const params = new URLSearchParams({
    response_type: 'code',
    client_id: clientId,
    redirect_uri: redirectUri,
    scope: p.scope,
    state,
  });
  if (p.usesPKCE) {
    params.set('code_challenge', challenge);
    params.set('code_challenge_method', 'S256');
  }
  return `${p.authorizeUrl}?${params.toString()}`;
}

// Exchange the authorization code for an access token. `fetchImpl` defaults to
// the global fetch; tests inject a stub.
async function exchangeCode(name, { code, redirectUri, verifier }, fetchImpl) {
  const p = PROVIDERS[name];
  const doFetch = fetchImpl || fetch;
  const clientId = process.env[p.clientIdEnv];
  const clientSecret = process.env[p.clientSecretEnv];
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: redirectUri,
    client_id: clientId,
  });
  if (p.usesPKCE && verifier) body.set('code_verifier', verifier);
  const headers = { 'Content-Type': 'application/x-www-form-urlencoded' };
  if (p.usesPKCE) {
    // X confidential clients authenticate with HTTP Basic on the token endpoint.
    headers.Authorization = 'Basic ' + Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
  } else {
    body.set('client_secret', clientSecret);
  }
  const resp = await doFetch(p.tokenUrl, { method: 'POST', headers, body: body.toString() });
  if (!resp.ok) {
    const t = await resp.text().catch(() => '');
    throw new Error(`token exchange failed (${resp.status}): ${t.slice(0, 200)}`);
  }
  const json = await resp.json();
  if (!json.access_token) throw new Error('no access_token in token response');
  return json.access_token;
}

// Fetch and normalize the provider profile → { providerId, email, avatarUrl }.
async function fetchProfile(name, accessToken, fetchImpl) {
  const p = PROVIDERS[name];
  const doFetch = fetchImpl || fetch;
  const resp = await doFetch(p.profileUrl, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!resp.ok) {
    const t = await resp.text().catch(() => '');
    throw new Error(`profile fetch failed (${resp.status}): ${t.slice(0, 200)}`);
  }
  const parsed = p.parseProfile(await resp.json());
  if (!parsed.providerId || parsed.providerId === 'undefined') {
    throw new Error('provider profile had no id');
  }
  return parsed;
}

module.exports = {
  PROVIDERS,
  providerConfig,
  isProviderConfigured,
  configuredProviders,
  buildAuthorizeUrl,
  exchangeCode,
  fetchProfile,
  pkcePair,
  randomState,
};
