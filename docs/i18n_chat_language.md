# Multilingual AI chat — reply in the user's language

The RUNECLAW UI dictionary (`bot/utils/i18n.py`) is English + Traditional
Chinese. The **conversational** surface, however, is the largest volume of
user-facing text and the hardest to key by hand — so it's localized a
different way: the LLM is simply told which language to answer in, and it
translates its own reply. One directive covers **both** the Telegram bot and
the website chat, because both funnel through `_llm_chat`.

## The wiring

`bot/skills/telegram_handler.py::_llm_chat(..., reply_lang="")` appends a
LANGUAGE directive to the system prompt when `reply_lang` names a non-English
language (via `i18n.chat_language_name`). English / empty / unknown → no
directive, so the default English persona stands. This applies to the authed
and public prompts alike.

Where `reply_lang` comes from:

- **Telegram** (`_handle_message`): an explicit `/lang` choice wins
  (`get_user_lang_raw`); otherwise it auto-detects from the Telegram client's
  `language_code` — which nothing read before now. So a Spanish/French/… user
  gets native chat with no setup.
- **Web** (`bot/web/user_gateway.py::handle_chat` / `handle_public_chat`):
  reads `lang` from the request. The Express side (`app/routes/chat.js`)
  forwards the user's `prefs.lang`; the pref is whitelisted+normalized in
  `app/routes/profile.js::sanitizePrefs` against the same language set as
  `i18n._CHAT_LANG_NAMES`.

## Discipline

- **Broader than the UI dictionary on purpose.** The LLM can reply in any of
  the named languages (`_CHAT_LANG_NAMES`); the `t()` dictionary stays en/zh.
  `t()` already falls back to English for an unknown language, so storing a
  broader code never breaks UI strings.
- **Unset ≠ English.** `get_user_lang_raw` / `UserStore.get_lang` preserve the
  "never chose" signal so auto-detection can kick in; `get_user_lang` still
  flattens to `en` for the UI.
- **Symbols stay put.** The directive tells the model to keep ticker symbols,
  numbers, and code identifiers unchanged while translating prose.

## Tests

- `tests/test_i18n_chat_lang.py` — the code→name map, region-subtag
  normalization, the raw/unset reader, and the audited `set_lang`/`get_lang`
  store methods (+ `set_user_lang` validation).
- `tests/test_web_gateway.py` — `lang` in the chat request reaches `_llm_chat`
  as `reply_lang`, on both the authed and public paths.

## Next

A web language switcher (Account view) + browser `navigator.language`
auto-detect writes `prefs.lang`, activating web chat localization end-to-end;
that lands with the web-UI i18n foundation (the follow-up slice).
