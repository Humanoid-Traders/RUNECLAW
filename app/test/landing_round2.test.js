/**
 * UX-3: landing round 2 — the last three verified landing findings.
 *
 * 1. "800+ symbols scanned" now has living proof: a market strip fed by the
 *    server-cached tickers endpoint, refreshing in place.
 * 2. The verifiable leaderboard — the platform's differentiator — is teased
 *    on the landing itself: top rows, percent/ratio fields only.
 * 3. Invited (?ref=) visitors are recognized in the HERO, not only in a
 *    muted paragraph buried in the register card below the fold.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const landing = fs.readFileSync(
  path.join(__dirname, '..', 'public', 'index.html'), 'utf8');

test('market strip exists, feeds from the cached tickers API, refreshes', () => {
  assert.ok(landing.includes('id="tickerStrip"'));
  assert.match(landing, /fetch\('\/api\/market\/tickers'\)/);
  assert.match(landing, /setInterval\(draw, 15000\)/);
  // Symbols are sanitized before touching the DOM (venue-supplied strings).
  assert.match(landing, /replace\(\/\[\^A-Z0-9\]\/gi, ''\)/);
});

test('leaderboard tease renders percent/ratio fields only — never dollars', () => {
  assert.ok(landing.includes('id="boardTease"'));
  assert.match(landing, /fetch\('\/api\/public\/leaderboard'\)/);
  const tease = landing.slice(landing.indexOf('Leaderboard tease'),
    landing.indexOf('Honest hero'));
  assert.ok(tease.includes('profit_factor') && tease.includes('sharpe')
    && tease.includes('round_trips'));
  assert.ok(!/pnl_usd|equity_usd|net_pnl/.test(tease),
    'no dollar field may reach the public tease');
  // Handles are escaped — they are user-chosen strings.
  assert.match(tease, /esc\(row\.handle/);
});

test('invited visitors are recognized in the hero, register note kept', () => {
  assert.ok(landing.includes("_b.id='inviteHero'"));
  // Inserted before the hero h1 — first thing an invited visitor sees.
  assert.match(landing, /_hero\.insertBefore\(_b,_h1\)/);
  // The original register-card note still exists.
  assert.ok(landing.includes("_n.id='inviteNote'"));
  // The handle upgrade reaches BOTH notes.
  assert.match(landing, /getElementById\('inviteHero'\)/);
});
