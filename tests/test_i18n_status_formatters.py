"""
i18n for the /status dashboard and the /open_positions text fallback
(render_status_card, render_open_positions in rich_cards.py).

Both return Telegram HTML strings, so CJK renders natively — no PNG-font work
needed. English output is byte-identical to before; zh users get translations.
"""

from bot.formatters.rich_cards import render_open_positions, render_status_card


def _status(**kw):
    base = dict(mode="LIVE", active=True, equity=1000.0, open_positions=2,
                daily_pnl=1.5, drawdown=2.0, max_drawdown=10.0,
                market_bias="Bull", pending_ideas=3, lang="en")
    base.update(kw)
    return render_status_card(**base)


class TestStatusCardEnglish:
    def test_header_status_mode_line(self):
        out = _status()
        assert "\U0001f7e2 ACTIVE | \U0001f534 LIVE | Bitget" in out
        assert "<b>RUNECLAW STATUS</b> — " in out

    def test_sections_and_labels(self):
        out = _status()
        for frag in ("<b>Engine</b>", "- State: Active", "- Mode: LIVE",
                     "- Market Bias: Bull", "- Pending Ideas: 3",
                     "<b>Capital</b>", "- Equity:", "- Open Positions: 2",
                     "- Daily PnL:", "<b>Risk</b>", "- Drawdown:", " limit"):
            assert frag in out, frag

    def test_halted_paper_variant(self):
        out = _status(active=False, mode="PAPER")
        assert "\U0001f534 HALTED | \U0001f7e1 PAPER | Bitget" in out
        assert "- State: Halted (circuit breaker)" in out


class TestOpenPositionsEnglish:
    def test_empty_state(self):
        assert render_open_positions([], "en") == (
            "No open positions right now.\n"
            "Say \"scan\" or \"analyze BTC\" to find setups.")

    def test_position_row(self):
        pos = {"pair": "ETH/USDT", "direction": "LONG", "entry": 100, "current": 110,
               "pnl_pct": 10, "size_usd": 500, "sl": 0, "tp": 0,
               "sl_order": "exchange", "untracked": True, "hold_hours": 2}
        out = render_open_positions([pos], "en")
        assert "<b>Open Positions (1)</b>" in out and " total" in out
        assert "  SL <i>None</i> / TP <i>None</i> on exchange" in out
        assert "⚠️ <i>Untracked — opened outside bot</i>" in out


class TestChineseDiffers:
    def test_status_translated(self):
        en = _status(lang="en")
        zh = _status(lang="zh")
        assert en != zh
        assert "運作中" in zh and "引擎" in zh and "RUNECLAW STATUS" not in zh

    def test_open_positions_translated(self):
        assert render_open_positions([], "zh") != render_open_positions([], "en")
