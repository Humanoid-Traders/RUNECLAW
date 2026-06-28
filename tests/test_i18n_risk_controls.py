"""
i18n routing for /risk (stats card + control buttons), /reset, and /whynot.

Same contract: English byte-identical to the old literals (emoji/HTML wrappers
stay in code), zh users get Traditional translations. Locks the English wording
and reconstructs the old /reset lines exactly (incl. placeholders).
"""

from bot.utils.i18n import _STRINGS, t

RISK_CONTROLS_EN = {
    "lbl_risk_title": "RISK",
    "lbl_daily_loss_limit": "Daily Loss Limit",
    "lbl_current_drawdown": "Current Drawdown",
    "lbl_open_trades": "Open Trades",
    "lbl_leverage_cap": "Leverage Cap",
    "lbl_circuit_breaker": "Circuit Breaker",
    "val_tripped": "TRIPPED",
    "val_ok": "OK",
    "btn_safe_mode": "Safe Mode",
    "btn_pause": "Pause",
    "btn_stop_bot": "Stop Bot",
    "invalid_symbol_format": "Invalid symbol format.",
    "reset_cb_done": "<b>Circuit breaker reset</b>\n\nTrading resumed.",
    "reset_streak_cleared": "<b>Streak cleared</b>  {n} → 0",
    "reset_nothing": "<b>Nothing to reset</b>\n\nCB: off  •  Streak: {n}",
}


def test_english_is_byte_identical():
    for key, literal in RISK_CONTROLS_EN.items():
        assert t(key, "en") == literal, f"{key}: English wording changed"


def test_keys_have_nonempty_chinese():
    for key in RISK_CONTROLS_EN:
        assert key in _STRINGS
        assert _STRINGS[key]["zh"].strip()


def test_reset_lines_reconstruct_exactly():
    assert (f"\U0001f7e2 {t('reset_cb_done', 'en')}"
            == "\U0001f7e2 <b>Circuit breaker reset</b>\n\nTrading resumed.")
    assert (f"\U0001f7e2 {t('reset_streak_cleared', 'en', n=5)}"
            == "\U0001f7e2 <b>Streak cleared</b>  5 → 0")
    assert (f"\U0001f7e1 {t('reset_nothing', 'en', n=2)}"
            == "\U0001f7e1 <b>Nothing to reset</b>\n\nCB: off  •  Streak: 2")


def test_substantive_labels_translated_in_chinese():
    for key in ("lbl_risk_title", "lbl_circuit_breaker", "btn_safe_mode",
                "invalid_symbol_format", "reset_cb_done"):
        assert t(key, "zh") != t(key, "en")
