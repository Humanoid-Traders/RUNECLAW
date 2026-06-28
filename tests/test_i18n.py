"""
i18n: t() behaviour, language get/set, and translation-table integrity.

Includes a regression guard that no Traditional-Chinese (zh) string contains a
Simplified-only character — the exact bug that shipped in the /holdtime help
line ("持仓时间与胜率分析" → "持倉時間與勝率分析").
"""

import threading

from bot.utils.i18n import (
    DEFAULT_LANG,
    SUPPORTED_LANGS,
    _STRINGS,
    get_user_lang,
    set_user_lang,
    t,
)


class TestTranslate:
    def test_english_and_chinese(self):
        assert t("confirm", "en") == "Confirm"
        assert t("confirm", "zh") == "確認"

    def test_missing_key_returns_key(self):
        assert t("nope_not_a_key", "en") == "nope_not_a_key"

    def test_missing_lang_falls_back_to_english(self):
        assert t("confirm", "fr") == "Confirm"

    def test_default_lang_is_english(self):
        assert t("confirm") == t("confirm", DEFAULT_LANG) == "Confirm"

    def test_placeholder_substitution(self):
        assert t("analyzing", "en", asset="BTC") == "Analyzing BTC..."
        assert t("analyzing", "zh", asset="BTC") == "分析 BTC 中..."

    def test_partial_kwargs_do_not_raise(self):
        # Missing placeholder → template returned unfilled, never an exception.
        out = t("trade_executed", "en", asset="BTC")  # missing direction/price
        assert isinstance(out, str) and "{direction}" in out


class TestTableIntegrity:
    def test_every_key_has_both_languages_nonempty(self):
        for key, entry in _STRINGS.items():
            for lang in SUPPORTED_LANGS:
                assert lang in entry, f"{key} missing '{lang}'"
                assert entry[lang].strip(), f"{key}.{lang} is empty"

    def test_key_count_is_stable(self):
        # Guards against an accidental mass-deletion; grows intentionally.
        assert len(_STRINGS) >= 47

    def test_no_simplified_chinese_in_zh_strings(self):
        # Curated set of Simplified-only characters (Traditional form differs),
        # covering trading/UI vocabulary. None may appear in a zh value.
        SIMPLIFIED = set(
            "仓单账盘额时间胜负关闭开实测优损风报错价涨数据个务动执"
            "后来国币笔总资产击显标这钟历习题长门问观达买卖"
        )
        offenders = {}
        for key, entry in _STRINGS.items():
            hits = sorted({c for c in entry.get("zh", "") if c in SIMPLIFIED})
            if hits:
                offenders[key] = hits
        assert not offenders, f"Simplified chars in zh strings: {offenders}"


class _FakeStore:
    """Minimal stand-in for UserStore (get / _lock / _users / _save)."""

    def __init__(self, users=None):
        self._users = dict(users or {})
        self._lock = threading.Lock()
        self.saves = 0

    def get(self, tg_id):
        return self._users.get(str(tg_id))

    def _save(self):
        self.saves += 1


class TestUserLang:
    def test_get_defaults_to_english(self):
        store = _FakeStore({"42": {"authorized": True}})
        assert get_user_lang(store, "42") == "en"

    def test_get_none_store(self):
        assert get_user_lang(None, "42") == DEFAULT_LANG

    def test_set_and_get_roundtrip(self):
        store = _FakeStore({"42": {"authorized": True}})
        assert set_user_lang(store, "42", "zh") is True
        assert store.saves == 1
        assert get_user_lang(store, "42") == "zh"

    def test_set_rejects_unsupported_lang(self):
        store = _FakeStore({"42": {"authorized": True}})
        assert set_user_lang(store, "42", "jp") is False
        assert store.saves == 0

    def test_set_unknown_user_is_false(self):
        store = _FakeStore({})
        assert set_user_lang(store, "999", "zh") is False


def test_supported_langs_are_translatable():
    # Every advertised language must actually resolve for a representative key.
    for lang in SUPPORTED_LANGS:
        assert t("confirm", lang)
