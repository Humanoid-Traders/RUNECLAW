"""
i18n routing for /analyze and /open_positions status strings.

Same contract as the /portfolio pass: English output is byte-identical to the
old hardcoded literals (wrappers/emoji stay in code), and zh users get
Traditional translations. These tests lock the English wording and verify the
reconstructed lines match the originals exactly.
"""

import html

from bot.utils.i18n import _STRINGS, t

ANALYZE_POSITIONS_EN = {
    "analyze_invalid_symbol": "Invalid symbol. Use format: <code>BTC</code> or <code>BTC/USDT</code>",
    "analyze_usdt_self": "Cannot analyze USDT against itself. Provide a token symbol, e.g. <code>BTC</code>",
    "analyze_failed": "Analysis failed for <code>{symbol}</code>: {detail}",
    "positions_none": 'No open positions or pending orders right now.\nSay "scan" or "analyze BTC" to find setups.',
    "positions_none_short": "No open positions right now.",
    "hdr_open_positions_title": "OPEN POSITIONS",
    "lbl_total": "total",
}


def test_english_is_byte_identical():
    for key, literal in ANALYZE_POSITIONS_EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"


def test_keys_have_nonempty_chinese():
    for key in ANALYZE_POSITIONS_EN:
        assert key in _STRINGS
        assert _STRINGS[key]["zh"].strip()


def test_analyze_failed_placeholders():
    sym, det = "ETH/USDT", "boom"
    out = t("analyze_failed", "en", symbol=html.escape(sym), detail=html.escape(det))
    assert out == f"Analysis failed for <code>{sym}</code>: {det}"
    # zh keeps the same placeholders.
    zh = t("analyze_failed", "zh", symbol=sym, detail=det)
    assert sym in zh and det in zh


def test_reconstructed_lines_match_old_format():
    # /analyze error wrappers
    assert (f"\U0001f534 {t('analyze_invalid_symbol', 'en')}"
            == "\U0001f534 Invalid symbol. Use format: <code>BTC</code> or <code>BTC/USDT</code>")
    # /open_positions header
    hdr = (f"\U0001f4ca <b>{t('hdr_open_positions_title', 'en')} (2)</b> "
           f"\U0001f7e2 {1.5:+.2f}% {t('lbl_total', 'en')}")
    assert hdr == "\U0001f4ca <b>OPEN POSITIONS (2)</b> \U0001f7e2 +1.50% total"


def test_strings_actually_translated():
    for key in ("analyze_invalid_symbol", "positions_none", "hdr_open_positions_title"):
        assert t(key, "zh") != t(key, "en")
