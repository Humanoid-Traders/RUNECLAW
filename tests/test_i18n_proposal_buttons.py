"""
i18n for the trade-proposal buttons (Take it / Limit / Skip, on every signal
card) and the trade-execution result messages.

English byte-identical; zh users get translations. Telegram renders the CJK
button labels natively (no font dependency, unlike the PNG cards).
"""

from bot.utils.i18n import _STRINGS, t

EN = {
    "btn_take_it": "Take it",
    "lbl_limit": "Limit",
    "btn_skip": "Skip",
    "trade_executed_ok": "<b>Trade executed!</b>",
    "trade_executed_fail": "<b>Trade didn't go through</b>",
}


def test_english_is_byte_identical():
    for key, literal in EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"


def test_keys_have_nonempty_chinese():
    for key in EN:
        assert key in _STRINGS
        assert _STRINGS[key]["zh"].strip()


def test_exec_result_wrappers_reconstruct():
    assert (f"✅ {t('trade_executed_ok', 'en')}\n\n{{result}}"
            == "✅ <b>Trade executed!</b>\n\n{result}")
    assert (f"❌ {t('trade_executed_fail', 'en')}\n\n{{result}}"
            == "❌ <b>Trade didn't go through</b>\n\n{result}")


def test_buttons_translated_in_chinese():
    for key in ("btn_take_it", "lbl_limit", "btn_skip"):
        assert t(key, "zh") != t(key, "en")
