# Web i18n — client localizer + language switcher

Slice 2 of multi-language (Slice 1 was the AI-chat reply-language directive).
The web app had zero i18n; this adds a tiny, dependency-free client localizer
and a language switcher, applied first to the landing page frame (the
conversion-critical path: nav, hero, sign-up, footer).

## How it works

`app/public/js/i18n.js` swaps text marked up with data attributes:

- `data-i18n="key"` → `textContent`
- `data-i18n-html="key"` → `innerHTML` (for copy with inline `<span>`/`<br>`)
- `data-i18n-attr="placeholder:key;aria-label:key2"` → attributes

The **English text stays in the HTML** as the source-of-truth fallback, so any
un-keyed or un-translated string renders in English — never blank. Adding a
language to a key, or a new key, is the only work; the markup already carries
the English.

**Language resolution:** saved choice (`localStorage.rc_lang`) →
`navigator.language` → English. Choosing a language:
- persists `rc_lang`,
- sets `<html lang>` and `dir` (`rtl` for Arabic),
- re-applies translations,
- and, for a logged-in user, writes `prefs.lang` via `PUT /api/profile` — so
  the **AI chat replies in the same language** (Slice 1 wiring). This is the
  seam that connects the two slices.

Offered languages: English, Español, 繁體中文, Português, Français, العربية.
The `<select>` switcher auto-injects into the top nav (or any
`[data-i18n-switcher]` host).

## Discipline

- **Dual-mode module**: the pure helpers (`normalize`/`resolveLang`/
  `translate`) are exported under Node for tests; the browser path
  self-initializes and exposes `window.RCI18N`.
- **No silent gaps**: a test asserts every dictionary key defines all six
  offered languages, so a half-added key fails CI rather than shipping English
  where a translation was expected.
- **Incremental by design**: only the landing-page frame is keyed so far;
  un-keyed sections fall back to English. The dashboard (`dashboard.js`, the
  bulk of in-app copy) is the next slice.

## Tests

`app/test/i18n.test.js` — normalization, resolution precedence, translate +
English fallback, and the all-languages-present integrity check. 5 tests;
the DOM apply/switcher paths were smoke-verified against a minimal DOM shim.

## Next

Slice 3: extract `app/public/js/dashboard.js` copy behind the same `t()` +
`data-i18n` convention (the logged-in surface). Slice 4: key the remaining
inline Telegram-bot replies and optionally add a 3rd UI language to the bot
dictionary.
