"""
/trade i18n routing — format help, invalid-trade error, confirmation card,
and the Confirm/Cancel buttons.

Same contract: English byte-identical to the old literals (emoji/HTML wrappers
stay in code), zh users get Traditional translations. Locks the English wording
and reconstructs the old lines exactly.
"""

import html

from bot.utils.i18n import _STRINGS, t

TRADE_EN = {
    "trade_invalid": "<b>Invalid trade:</b> {detail}",
    "lbl_manual_trade": "Manual Trade",
    "lbl_margin": "Margin",
    "lbl_type": "Type",
    "lbl_rr": "R:R",
    "trade_reduced_checks": "Reduced risk checks for manual orders",
    "trade_margin_auto": "Auto (risk-based)",
    # reused keys the card relies on
    "entry": "Entry",
    "lbl_sl": "SL",
    "lbl_tp": "TP",
    "confirm": "Confirm",
    "cancel": "Cancel",
}

TRADE_HELP_EN = (
    "<b>Manual Trade</b>\n\n"
    "Format:\n"
    "<code>/trade buy SOL 71.42 sl 70.05 tp 76.42</code>\n"
    "<code>/trade short ETH 1721 sl 1695 tp 1842 margin 250</code>\n\n"
    "• <code>buy/long</code> = LONG\n"
    "• <code>sell/short</code> = SHORT\n"
    "• <code>margin</code> = optional fixed margin in USD"
)


def test_english_is_byte_identical():
    for key, literal in TRADE_EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"
    assert t("trade_help", "en") == TRADE_HELP_EN


def test_keys_have_nonempty_chinese():
    for key in list(TRADE_EN) + ["trade_help"]:
        assert key in _STRINGS
        assert _STRINGS[key]["zh"].strip()


def test_help_keeps_command_examples_literal():
    # Command examples must stay in English even in the zh string.
    zh = t("trade_help", "zh")
    assert "/trade buy SOL 71.42 sl 70.05 tp 76.42" in zh
    assert "<b>手動交易</b>" in zh


def test_invalid_trade_placeholder():
    out = t("trade_invalid", "en", detail=html.escape("x<y"))
    assert out == "<b>Invalid trade:</b> x&lt;y"


def test_reconstructed_card_lines_match_old_format():
    L = "en"
    assert (f"{t('entry', L)}:  <code>$1.0000</code>"
            == "Entry:  <code>$1.0000</code>")
    assert (f"{t('lbl_rr', L)}:    <code>2.00</code>"
            == "R:R:    <code>2.00</code>")
    assert (f"{t('lbl_type', L)}:   LIMIT" == "Type:   LIMIT")
    assert ("✅ " + t("confirm", L) == "✅ Confirm")
    assert ("❌ " + t("cancel", L) == "❌ Cancel")


def test_card_labels_translated_in_chinese():
    for key in ("lbl_manual_trade", "lbl_margin", "lbl_type", "trade_reduced_checks"):
        assert t(key, "zh") != t(key, "en")
