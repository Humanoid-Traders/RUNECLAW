/**
 * UX-6: web community/growth — social share cards, your-real-rank display,
 * the verified-chip + publish-hash board tease, and the live-season countdown.
 *
 * The rank BACKEND is covered live in leaderboard.test.js. The pieces below are
 * DOM/meta surfaces with no headless harness, so they're verified by source
 * assertion. Everything here is §4-safe: percent / position / hash only —
 * never a dollar figure on a public or community surface.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const pub = (f) => fs.readFileSync(path.join(__dirname, '..', 'public', f), 'utf8');
const index = pub('index.html');
const leaderboard = pub('leaderboard.html');
const dash = pub('js/dashboard.js');

// Every shareable public page carries an absolute-URL OG + Twitter card so a
// pasted link unfurls with our image and copy (unfurlers ignore relative paths).
const SHARE_PAGES = ['leaderboard.html', 'agent.html', 'agent-card.html',
  'proof.html', 'track.html', 'letter.html'];

test('shareable public pages carry absolute-URL OG + Twitter share cards', () => {
  for (const f of SHARE_PAGES) {
    const html = pub(f);
    assert.match(html, /<meta property="og:title"/, `${f} needs og:title`);
    assert.match(html, /<meta property="og:image" content="https:\/\/[^"]+\/og_image_1200x630\.jpg"/,
      `${f} needs an absolute og:image`);
    assert.match(html, /<meta name="twitter:card" content="summary_large_image"/,
      `${f} needs a twitter summary card`);
    assert.match(html, /<meta property="og:url" content="https:\/\//, `${f} needs an absolute og:url`);
  }
});

test('no dollar sign leaks into any share-card meta description (§4)', () => {
  for (const f of SHARE_PAGES) {
    const html = pub(f);
    const metas = html.match(/<meta [^>]*(og:|twitter:)[^>]*>/g) || [];
    for (const m of metas) {
      assert.ok(!/\$\d/.test(m), `${f} share meta must not carry a dollar figure: ${m}`);
    }
  }
});

test('the landing board tease shows the verified chip + a re-verifiable publish hash', () => {
  assert.match(index, /chip chip--up">✓ verified/);
  assert.match(index, /row\.publish_hash/);
  assert.match(index, /String\(row\.publish_hash\)\.slice\(0, 10\)/);
  // The hash links to the full board where it can be re-derived.
  assert.match(index, /href="\/leaderboard" title="Re-derive the hash yourself"/);
  // A Proof column header was added for it.
  assert.match(index, /<th class="r">Round trips<\/th><th>Proof<\/th>/);
});

test('the dashboard shows the caller their REAL rank (position-only, no dollars)', () => {
  assert.match(dash, /data\.my_rank/);
  assert.match(dash, /of \$\{total\} ranked agent/);
  // Unranked members are invited to close a trade, not shown a fake #0.
  assert.match(dash, /Close a trade to get ranked/);
  // No dollar figure in the rank line.
  assert.ok(!/#\$\{data\.my_rank\}[^`]*\$\d/.test(dash), 'rank line must not carry a dollar figure');
});

test('the public leaderboard counts down to the live season seal (UTC, client-only)', () => {
  assert.match(leaderboard, /function renderCountdown\(\)/);
  assert.match(leaderboard, /Live season/);
  assert.match(leaderboard, /seals in/);
  // Freezes at month end in UTC, and hides itself when a frozen season is shown.
  assert.match(leaderboard, /Date\.UTC\(now\.getUTCFullYear\(\), now\.getUTCMonth\(\) \+ 1, 1/);
  assert.match(leaderboard, /if \(activeSeason\) \{[^}]*countdownEl\.style\.display = 'none'/s);
  // Ticks without a hard reload.
  assert.match(leaderboard, /setInterval\(renderCountdown, 60000\)/);
});
