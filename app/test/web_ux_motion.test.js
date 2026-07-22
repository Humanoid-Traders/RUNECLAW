/**
 * WEB-UX premium motion pass: a single easing/duration vocabulary, cohesive
 * interaction states (button lift, card hover, input glow), a view cross-fade
 * on real navigation, and a first-load count-up — all auto-gated by the global
 * prefers-reduced-motion block. Verified by source assertion.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');

const css = fs.readFileSync(path.join(__dirname, '..', 'public', 'styles.css'), 'utf8');
const dash = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'dashboard.js'), 'utf8');
const app = fs.readFileSync(path.join(__dirname, '..', 'public', 'js', 'app.js'), 'utf8');

test('a single motion vocabulary (easing + durations) is defined as tokens', () => {
  assert.match(css, /--ease-out:\s*cubic-bezier/);
  assert.match(css, /--ease-spring:\s*cubic-bezier/);
  assert.match(css, /--dur-1:.*--dur-2:.*--dur-3:/s);
});

test('interaction states use the tokens: button lift, input focus glow', () => {
  assert.match(css, /\.btn:hover:not\(:disabled\)\s*\{[^}]*translateY\(-1px\)/);
  assert.match(css, /\.btn:active:not\(:disabled\)\s*\{[^}]*scale\(/);
  assert.match(css, /\.input:focus[^{]*\{[^}]*box-shadow:\s*0 0 0 3px var\(--gold-dim\)/);
});

test('a view cross-fade keyframe exists and binds to the view container', () => {
  assert.match(css, /@keyframes rc-view-in/);
  assert.match(css, /#viewContainer\.view-anim\s*\{[^}]*animation:\s*rc-view-in/);
});

test('motion is accessible by default (reduced-motion neutralises durations)', () => {
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(css, /transition-duration:\s*\.01ms\s*!important/);
});

test('showView triggers the cross-fade only on real navigation (not soft SSE)', () => {
  // The class is added inside the `if (!opts.soft)` branch, with a reflow
  // restart so repeat navigations still animate.
  assert.match(dash, /if \(!opts\.soft\) \{[\s\S]*offsetWidth[\s\S]*classList\.add\('view-anim'\)/);
});

test('count-up helper is exported and honours reduced motion', () => {
  assert.match(app, /countUp, animateCounters,/);           // exported on RC
  assert.match(app, /function countUp\(/);
  assert.match(app, /prefers-reduced-motion: reduce/);       // instant when reduced
  // First-load only: soft refreshes must not re-roll the number.
  assert.match(app, /wasLoaded[\s\S]*animateCounters\(el\)/);
});

test('premium-smoothness pass: rail indicator + tactile rows are token-driven', () => {
  // Active rail item grows an accent bar; hovered items glide toward content.
  assert.match(css, /\.rail a\[aria-current="page"\]::before\s*\{[^}]*height:/);
  assert.match(css, /\.rail a:hover:not\(\[aria-current="page"\]\)\s*\{[^}]*translateX/);
  // Table rows get a left accent rail on hover (pure paint, no layout shift).
  assert.match(css, /\.tbl tbody tr:hover\s*\{[^}]*inset 2px 0 0 var\(--gold\)/);
});

test('scroll-reveal is opt-in and reduced-motion safe in both CSS and JS', () => {
  // Hidden pre-state + revealed state.
  assert.match(css, /\.reveal-on-scroll\s*\{[^}]*opacity:\s*0/);
  assert.match(css, /\.reveal-on-scroll\.rc-inview\s*\{[^}]*opacity:\s*1/);
  // Reduced-motion users never get a hidden pre-state (never stranded blank).
  assert.match(css, /prefers-reduced-motion: reduce\)\s*\{\s*\.reveal-on-scroll\s*\{[^}]*opacity:\s*1/);
  // JS observer is exported, uses IntersectionObserver, and falls back to
  // instant reveal when motion is reduced or the API is unavailable.
  assert.match(app, /revealOnScroll,/);                      // exported on RC
  assert.match(app, /function revealOnScroll\(/);
  assert.match(app, /_reducedMotion\(\) \|\| !\('IntersectionObserver' in window\)/);
});

test('landing marks below-the-fold sections for scroll-reveal', () => {
  const html = fs.readFileSync(path.join(__dirname, '..', 'public', 'index.html'), 'utf8');
  assert.ok((html.match(/class="section reveal-on-scroll"/g) || []).length >= 3);
});
