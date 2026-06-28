"""
i18n routing for the trade-confirmation flow messages (limit set / confirmed,
live-trading-not-enabled denial, trade-expired, limit-input cancelled).

English byte-identical to the old literals (emoji/HTML wrappers stay in code),
zh users get Traditional translations. Reconstructs the multi-part lines
exactly, including placeholders.
"""

from bot.utils.i18n import _STRINGS, t

TRADE_FLOW_EN = {
    "trade_expired_rescan": "<b>Trade expired.</b> Run a new scan.",
    "limit_set_line": "<b>Limit set: {pair} {direction}</b>\nEntry: <code>{old}</code> → <code>{new}</code>",
    "confirmed_executing": "<b>Confirmed — executing...</b>",
    "live_not_enabled": "<b>Live trading not enabled</b>\n\nAsk an admin to grant you live trading access with /grant_live.",
    "limit_input_cancelled": "Limit price cancelled. Use the buttons to confirm or skip.",
}


def test_english_is_byte_identical():
    for key, literal in TRADE_FLOW_EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"


def test_keys_have_nonempty_chinese():
    for key in TRADE_FLOW_EN:
        assert key in _STRINGS
        assert _STRINGS[key]["zh"].strip()


def test_limit_set_block_reconstructs_exactly():
    L = "en"
    full = (f"\U0001f4b0 {t('limit_set_line', L, pair='ZEC', direction='SHORT', old='$393.0900', new='$389.4200')}"
            f"\n\n✅ {t('confirmed_executing', L)}")
    assert full == (
        "\U0001f4b0 <b>Limit set: ZEC SHORT</b>\n"
        "Entry: <code>$393.0900</code> → <code>$389.4200</code>\n\n"
        "✅ <b>Confirmed — executing...</b>"
    )


def test_live_not_enabled_wrapper():
    assert (f"\U0001f512 {t('live_not_enabled', 'en')}"
            == "\U0001f512 <b>Live trading not enabled</b>\n\n"
               "Ask an admin to grant you live trading access with /grant_live.")


def test_translated_in_chinese():
    for key in TRADE_FLOW_EN:
        assert t(key, "zh") != t(key, "en")
