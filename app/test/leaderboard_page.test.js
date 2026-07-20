// Leaderboard A3 — the public page + Node relay. Mirrors the /proof serving
// chain: a no-auth relay of the gateway's ranked board, a page that renders it,
// and nav/footer surfacing. These lock the wiring so it can't silently regress.
const { test } = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const root = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const pub = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');

test('the public relay route is registered and page is served', () => {
  const server = root('server.js');
  assert.match(server, /\/api\/public\/leaderboard['"],\s*require\(['"]\.\/routes\/public_leaderboard['"]\)/);
  assert.match(server, /app\.get\(['"]\/leaderboard['"]/);
});

test('the relay is public (no auth middleware) and cached', () => {
  const r = root('routes/public_leaderboard.js');
  assert.match(r, /getGateway\([`'"]\/public\/leaderboard/);
  assert.doesNotMatch(r, /authMiddleware/);   // public by design
  assert.match(r, /CACHE_MS/);
});

test('the page fetches the public board and never shows a dollar figure', () => {
  const html = pub('leaderboard.html');
  assert.match(html, /fetch\(['"]\/api\/public\/leaderboard['"]/);
  assert.match(html, /publish_hash/);          // each row is re-verifiable
  // Size-agnostic display only — no leaked dollar fields rendered.
  for (const leaky of ['net_pnl', 'r.fees', 'max_dd']) {
    assert.ok(!html.includes(leaky), `page must not render ${leaky}`);
  }
});

test('leaderboard is surfaced in landing nav + footer, i18n in all six langs', () => {
  const index = pub('index.html');
  assert.ok((index.match(/href="\/leaderboard"/g) || []).length >= 2, 'nav + footer links');
  const i18n = pub('js/i18n.js');
  const line = i18n.split('\n').find((l) => l.includes("'nav.leaderboard'"));
  assert.ok(line, 'nav.leaderboard missing');
  for (const lang of ['en', 'es', 'zh', 'pt', 'fr', 'ar']) {
    assert.match(line, new RegExp(`${lang}:\\s*'`), `nav.leaderboard missing ${lang}`);
  }
});
