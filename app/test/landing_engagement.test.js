/**
 * UX-2: landing engagement + the §4 public-surface rule.
 *
 * The lead item is COMPLIANCE: the replay theater headlined the trade in
 * DOLLARS on the public landing page. The platform's own rule is that no
 * public community surface shows dollar amounts — the size-agnostic return
 * percent is reconstructible from the same recorded fill (pnl / size_usd),
 * so that is what the theater now shows. The rest makes the landing's live
 * proof real: honest LIVE/PAPER eyebrow, a stats strip from the recorded
 * track record (percent/count only), on-scroll theater reveal, and a
 * mind-stream whose timestamps don't freeze.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const read = (p) => fs.readFileSync(path.join(__dirname, '..', 'public', p), 'utf8');
const theater = read('js/theater.js');
const landing = read('index.html');

test('theater shows PERCENT return, never dollars (§4)', () => {
  assert.ok(!theater.includes("'-$'") && !theater.includes("'+$'"),
    'no dollar formatting may remain in the public theater');
  assert.match(theater, /pnl \/ size\) \* 100/);
  assert.match(theater, /toFixed\(2\) \+ '%'/);
  // No derivable percent -> an outcome word, still never a dollar figure.
  assert.match(theater, /'WIN' : 'LOSS'/);
  assert.ok(landing.includes('Return <b class="num" id="theaterPnl">'),
    'the summary row label must say Return, not PnL');
});

test('theater reveal waits until the section is actually seen', () => {
  const io = theater.indexOf("'IntersectionObserver' in window");
  assert.ok(io > 0);
  // The pre-reveal state is held hidden until the observer fires.
  assert.ok(theater.slice(io - 600, io + 600).includes('strokeDashoffset'));
});

test('hero eyebrow is honest about LIVE vs PAPER', () => {
  assert.match(landing, /d\.mode === 'LIVE' \? 'Live' : 'Paper mode'/);
  assert.match(landing, /removeAttribute\('data-i18n'\)/);
});

test('hero proof strip uses recorded percent/count fields only', () => {
  const strip = landing.slice(landing.indexOf('Honest hero'), landing.indexOf('heroStats'.repeat(1)) + 4000);
  assert.match(landing, /win_rate_pct/);
  assert.match(landing, /profit_factor/);
  assert.match(landing, /max_drawdown_pct/);
  // Never the dollar fields the same payload carries.
  assert.ok(!strip.includes('net_pnl_usd') && !strip.includes('current_equity_usd')
    && !strip.includes('avg_win_usd'),
    'the hero strip must not surface dollar stats on the public landing');
});

test('mind-stream ages refresh and live events rise in', () => {
  assert.match(landing, /data-rc-ago/);
  assert.match(landing, /setInterval\(function \(\) \{\s*host\.querySelectorAll\('\[data-rc-ago\]'\)/);
  assert.ok(landing.includes("firstElementChild.classList.add('rc-rise')"));
});
