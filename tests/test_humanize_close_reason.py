"""
humanize_close_reason() must never leak raw internal placeholder text (e.g.
"CLOSED (unknown)") into user-facing close notifications.

Real incident: a position closed with LiveExecutor._infer_close_reason()'s
deliberately-honest "CLOSED (unknown)" fallback (used when the exit price
doesn't clearly match SL or TP), and three separate renderers displayed that
raw string verbatim to the user -- in a photo caption, a text fallback, and
the close-confirmation PNG card -- reading as broken/placeholder text rather
than a clear "closed" outcome.
"""

from bot.formatters.signal_card import humanize_close_reason


class TestHumanizeCloseReason:
    def test_unknown_reason_drops_the_technical_qualifier(self):
        emoji, label = humanize_close_reason("CLOSED (unknown)", pnl_usd=0.17)
        assert "unknown" not in label.lower()
        assert "UNKNOWN" not in label.upper() or label.upper() == label  # no leak either way
        assert label == "Closed"
        assert emoji == "✅"  # win

    def test_unknown_reason_loss_gets_loss_emoji(self):
        emoji, label = humanize_close_reason("CLOSED (unknown)", pnl_usd=-0.05)
        assert label == "Closed"
        assert emoji == "❌"

    def test_tp_hit_inferred(self):
        emoji, label = humanize_close_reason("TP HIT (inferred)", pnl_usd=5.0)
        assert label == "Take-Profit Hit"
        assert emoji == "🎯"

    def test_sl_hit_inferred(self):
        emoji, label = humanize_close_reason("SL HIT (inferred)", pnl_usd=-3.0)
        assert label == "Stop-Loss Hit"
        assert emoji == "🛑"

    def test_stop_variant_matches_sl(self):
        emoji, label = humanize_close_reason("STOP_LOSS", pnl_usd=-1.0)
        assert label == "Stop-Loss Hit"

    def test_trailing_stop(self):
        emoji, label = humanize_close_reason("TRAILING_SL_HIT", pnl_usd=2.0)
        assert label == "Trailing Stop Hit"
        assert emoji == "🛑"

    def test_time_stop(self):
        emoji, label = humanize_close_reason("TIME_STOP", pnl_usd=0.0)
        assert label == "Time Stop"

    def test_liquidation(self):
        emoji, label = humanize_close_reason("LIQUIDATED", pnl_usd=-50.0)
        assert label == "Liquidated"
        assert emoji == "💥"

    def test_empty_reason_treated_as_unresolved(self):
        emoji, label = humanize_close_reason("", pnl_usd=1.0)
        assert label == "Closed"
        assert emoji == "✅"

    def test_none_reason_does_not_raise(self):
        emoji, label = humanize_close_reason(None, pnl_usd=1.0)
        assert label == "Closed"
