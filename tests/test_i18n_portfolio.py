"""
/portfolio i18n routing.

The /portfolio handler's labels (stats-card tiles + text fallback) are now
routed through t(). The contract: English output is BYTE-IDENTICAL to the old
hardcoded literals, and Traditional-Chinese users get translations. This test
locks the English value of every key the handler routes, so a future edit can't
silently change the live English wording.
"""

from bot.utils.i18n import _STRINGS, t

# Each key → the exact English literal it replaced in _cmd_portfolio.
PORTFOLIO_EN = {
    "portfolio_title": "YOUR PORTFOLIO",
    "portfolio_card_title": "PORTFOLIO",
    "lbl_equity": "Equity",
    "lbl_realized_pnl": "Realized PnL",
    "lbl_win_rate": "Win Rate",
    "lbl_win_rate_lc": "Win rate",
    "lbl_open_positions": "Open Positions",
    "lbl_total_trades": "Total Trades",
    "lbl_exposure": "Exposure",
    "lbl_max_drawdown": "Max Drawdown",
    "lbl_net_pnl": "Net PnL",
    "lbl_fees_paid": "Fees Paid",
    "lbl_unrealized_pnl": "Unrealized PnL",
    "lbl_cash": "Cash",
    "lbl_daily_pnl": "Daily PnL",
    "lbl_drawdown": "Drawdown",
    "lbl_size": "Size",
    "lbl_pnl": "PNL",
    "lbl_current": "Current",
    "lbl_limit": "Limit",
    "lbl_placed": "Placed",
    "lbl_net": "Net",
    "lbl_sl": "SL",
    "lbl_tp": "TP",
    "entry": "Entry",
    "hdr_open_positions": "Open Positions:",
    "hdr_pending_limits": "Pending Limit Orders:",
    "hdr_recent_trades": "Recent Trades:",
    "hdr_recent_trades_net": "Recent Trades (net of fees):",
    "lbl_session": "Session:",
    "portfolio_no_trades": 'No trades yet. Say "scan" to find signals.',
    "portfolio_no_live_trades": 'No live trades yet. Say "scan" to find signals.',
}


def test_english_is_byte_identical():
    for key, literal in PORTFOLIO_EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"


def test_every_key_exists_with_chinese():
    for key in PORTFOLIO_EN:
        assert key in _STRINGS, f"missing key {key}"
        assert _STRINGS[key]["zh"].strip(), f"{key} has empty zh"


def test_chinese_differs_from_english_for_words():
    # Pure-symbol labels (SL/TP/PNL) legitimately stay as-is or map to short
    # Chinese; the substantive labels must actually be translated.
    for key in ("portfolio_title", "lbl_equity", "lbl_open_positions",
                "hdr_recent_trades", "portfolio_no_trades"):
        assert t(key, "zh") != t(key, "en"), f"{key} not translated"


def test_reconstructed_lines_match_old_format():
    # Spot-check that wrapping the key in the surrounding literal reproduces the
    # exact old English line (the property the handler relies on).
    lang = "en"
    assert (f"\U0001f4bc <b>{t('portfolio_title', lang)}</b> (LIVE)"
            == "\U0001f4bc <b>YOUR PORTFOLIO</b> (LIVE)")
    assert (f"- {t('lbl_equity', lang)}: <code>$1.00</code>"
            == "- Equity: <code>$1.00</code>")
    assert (f"  {t('lbl_sl', lang)}: x | {t('lbl_tp', lang)}: y"
            == "  SL: x | TP: y")
    assert (f"<b>{t('hdr_recent_trades_net', lang)}</b>"
            == "<b>Recent Trades (net of fees):</b>")
