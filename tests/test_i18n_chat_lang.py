"""i18n chat reply-language helpers + first-class UserStore lang methods.

Covers the Slice-1 multilingual-chat wiring: the code→name map that decides
whether (and in which language) the LLM is told to reply, the raw 'unset'
signal used for auto-detection, and the audited store methods.
"""
import tempfile

from bot.utils import i18n
from bot.utils.user_store import UserStore


# ── chat_language_name / normalize_lang ──────────────────────────────────────

def test_normalize_strips_region_subtag():
    assert i18n.normalize_lang("pt-BR") == "pt"
    assert i18n.normalize_lang("zh_TW") == "zh"
    assert i18n.normalize_lang("ES") == "es"
    assert i18n.normalize_lang("") == ""
    assert i18n.normalize_lang(None) == ""


def test_chat_language_name_known_non_english():
    assert i18n.chat_language_name("es") == "Spanish"
    assert i18n.chat_language_name("fr") == "French"
    assert i18n.chat_language_name("zh-TW") == "Chinese"
    assert i18n.chat_language_name("pt-BR") == "Portuguese"


def test_chat_language_name_english_or_unknown_is_empty():
    # English / empty / unknown -> "" means "no directive, default English".
    assert i18n.chat_language_name("en") == ""
    assert i18n.chat_language_name("en-US") == ""
    assert i18n.chat_language_name("") == ""
    assert i18n.chat_language_name(None) == ""
    assert i18n.chat_language_name("xx") == ""      # not in the map


# ── UserStore first-class lang methods + raw reader ──────────────────────────

def _store():
    fd = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    fd.close()
    return UserStore(fd.name)


def test_store_get_lang_unset_then_set():
    s = _store()
    s.register("7", name="T")
    assert s.get_lang("7") is None            # unset -> None (the detect signal)
    assert s.set_lang("7", "zh") is True
    assert s.get_lang("7") == "zh"


def test_store_set_lang_unknown_user_fails():
    s = _store()
    assert s.set_lang("999", "es") is False


def test_get_user_lang_raw_preserves_unset():
    s = _store()
    s.register("7", name="T")
    # raw reader keeps the 'unset' distinction that get_user_lang() flattens.
    assert i18n.get_user_lang_raw(s, "7") is None
    assert i18n.get_user_lang(s, "7") == "en"   # flattened default
    i18n.set_user_lang(s, "7", "zh")
    assert i18n.get_user_lang_raw(s, "7") == "zh"


def test_set_user_lang_uses_store_and_validates():
    s = _store()
    s.register("7", name="T")
    assert i18n.set_user_lang(s, "7", "zh") is True
    assert s.get_lang("7") == "zh"
    # Unsupported UI language is rejected (dictionary is en/zh only).
    assert i18n.set_user_lang(s, "7", "es") is False
    assert s.get_lang("7") == "zh"
