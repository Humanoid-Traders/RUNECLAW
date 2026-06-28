"""
CJK font support in render_stats_card.

The /portfolio and /risk stats cards now receive zh-localized labels. PIL's
DejaVu font has no CJK glyphs, so those would render as tofu; render_stats_card
now switches to a CJK-capable font (which also covers Latin) when — and only
when — a label contains CJK. English cards never trip this, so their rendering
is unchanged.
"""

from bot.formatters.signal_card import _has_cjk, render_stats_card


def _is_png(b: bytes) -> bool:
    return isinstance(b, bytes) and b[:4] == b"\x89PNG"


class TestHasCjk:
    def test_detects_traditional_chinese(self):
        assert _has_cjk("權益")
        assert _has_cjk("投資組合")
        assert _has_cjk("Equity 權益")  # mixed

    def test_latin_and_symbols_are_not_cjk(self):
        assert not _has_cjk("Equity")
        assert not _has_cjk("$11,240.50")
        assert not _has_cjk("Win Rate 64%")
        assert not _has_cjk("")


class TestRenderStatsCard:
    def test_english_card_renders(self):
        png = render_stats_card({
            "title": "PORTFOLIO",
            "hero": {"label": "Equity", "value": "$11,240", "color": "white"},
            "tiles": [{"label": "Win Rate", "value": "64%", "color": "cyan"}],
        })
        assert _is_png(png)

    def test_chinese_card_renders(self):
        png = render_stats_card({
            "title": "投資組合",
            "subtitle": "LIVE · 16:30 UTC",
            "hero": {"label": "權益", "value": "$11,240.50", "color": "white"},
            "tiles": [
                {"label": "已實現損益", "value": "$1,240", "color": "green"},
                {"label": "勝率", "value": "64%", "color": "cyan"},
                {"label": "最大回撤", "value": "6.4%", "color": "red"},
            ],
        })
        assert _is_png(png)
        assert len(png) > 1000

    def test_empty_and_missing_fields_safe(self):
        assert _is_png(render_stats_card({}))
