"""
render_position_card's hero row (gross %, gross $) and its separate "NET PnL"
row (net-of-fees $) must each use their OWN sign for color -- not one sign
borrowed from the other.

Real incident: a WIF short showed "+0.60%" (a genuine gross gain) in RED,
immediately followed by a NEGATIVE dollar figure in the same line -- because
the hero row's color and its parenthetical dollar amount were both driven by
net_pnl (post-fee), while the percentage next to them was driven by pnl_pct
(gross). A small gain that fees ate into (gross +$0.19, net -$0.02 after
$0.21 fees -- both individually correct) rendered as a self-contradictory
"positive-looking number in loss color, followed by a negative dollar
amount." The separate, clearly-labeled "NET PnL" row already shows the
fee-adjusted figure; the hero row should consistently reflect the gross move.
"""

from unittest.mock import patch

from bot.formatters.signal_card import render_position_card

_BASE_DATA = {
    "symbol": "WIF/USDT", "direction": "SHORT", "is_live": True,
    "entry": 0.16640, "now": 0.16630,
    "size_usd": 31.97, "leverage": 10, "hold_time": "1.5h", "rr": 1.2,
    "sl": 0.17139, "tp": 0.16016, "sl_pct": 3.1, "tp_pct": 3.7,
    "sl_status": "on exchange", "tp_status": "on exchange",
}


def _is_png(b: bytes) -> bool:
    return isinstance(b, bytes) and b[:4] == b"\x89PNG"


def _captured_text_colors(data):
    """Render the card, returning (text, fill_color) for every draw.text call."""
    calls = []
    from PIL import ImageDraw
    original = ImageDraw.ImageDraw.text

    def _spy(self, xy, text, fill=None, font=None, *a, **kw):
        calls.append((text, fill))
        return original(self, xy, text, fill=fill, font=font, *a, **kw)

    with patch.object(ImageDraw.ImageDraw, "text", _spy):
        png = render_position_card(data)
    return png, calls


class TestPositionCardGrossVsNetColors:
    def test_gross_positive_net_negative_does_not_use_net_color_in_hero(self):
        # Gross: +0.60% / +$0.19 (favorable move). Net: -$0.02 after $0.21 fees.
        data = dict(_BASE_DATA, pnl_pct=0.60, pnl_usd=0.19, net_pnl=-0.02, fees=0.21)
        png, calls = _captured_text_colors(data)
        assert _is_png(png)

        hero_pct = next(t for t, _ in calls if t == "+0.60%")
        hero_usd = next((t, c) for t, c in calls if t == "  ($+0.19)")
        net_row = next((t, c) for t, c in calls if t.startswith("$-0.02"))

        # Hero % and hero $ share the SAME (gross-positive -> green) color.
        hero_pct_color = next(c for t, c in calls if t == hero_pct)
        assert hero_pct_color == hero_usd[1]
        assert hero_pct_color == (0, 200, 100)  # _GREEN
        # The dedicated NET PnL row is independently colored red (net is negative).
        assert net_row[1] == (230, 60, 60)  # _RED

    def test_gross_negative_net_positive_is_also_internally_consistent(self):
        # The inverse case: an unfavorable gross move that a rebate/adjustment
        # turns net-positive. Hero must stay red/gross; NET PnL row must be green.
        data = dict(_BASE_DATA, pnl_pct=-0.30, pnl_usd=-0.10, net_pnl=0.05, fees=0.15)
        png, calls = _captured_text_colors(data)
        assert _is_png(png)

        hero_usd_color = next(c for t, c in calls if t == "  ($-0.10)")
        net_row_color = next(c for t, c in calls if t.startswith("$+0.05"))
        assert hero_usd_color == (230, 60, 60)  # _RED (gross)
        assert net_row_color == (0, 200, 100)   # _GREEN (net)

    def test_aligned_signs_still_render_correctly(self):
        # Sanity: when gross and net agree (the common case), nothing regresses.
        data = dict(_BASE_DATA, pnl_pct=2.0, pnl_usd=5.0, net_pnl=4.5, fees=0.5)
        png, calls = _captured_text_colors(data)
        assert _is_png(png)
        assert any(t == "  ($+5.00)" and c == (0, 200, 100) for t, c in calls)
        assert any(t.startswith("$+4.50") and c == (0, 200, 100) for t, c in calls)
