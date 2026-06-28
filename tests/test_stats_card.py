"""
render_stats_card: the hero-number + tile-grid status card used by /portfolio
(and reusable for /performance, /risk). Display-only PNG renderer.
"""

import struct

from bot.formatters.signal_card import render_stats_card


def _dims(b: bytes):
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", b[16:24])


def test_renders_hero_and_tiles():
    png = render_stats_card({
        "title": "PORTFOLIO", "subtitle": "LIVE · 08:20 UTC",
        "hero": {"label": "Equity", "value": "$856.16", "color": "white"},
        "tiles": [
            {"label": "Realized PnL", "value": "+$42.10", "color": "green"},
            {"label": "Win Rate", "value": "61%", "color": "cyan"},
            {"label": "Open Positions", "value": "2", "color": "white"},
            {"label": "Max Drawdown", "value": "3.1%", "color": "red"},
        ],
        "footer": "Bitget USDT-M Futures",
    })
    w, h = _dims(png)
    assert w == 520 and h > 200


def test_renders_without_hero_or_footer():
    png = render_stats_card({"title": "RISK", "tiles": [
        {"label": "Daily Loss", "value": "1.2%", "color": "green"},
        {"label": "Circuit", "value": "OK", "color": "green"},
    ]})
    w, _ = _dims(png)
    assert w == 520


def test_unknown_color_falls_back_white():
    # A bad color key must not raise — defaults to white.
    png = render_stats_card({"title": "X", "tiles": [
        {"label": "A", "value": "1", "color": "not-a-color"}]})
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_more_tiles_taller_card():
    small = render_stats_card({"title": "X", "tiles": [{"label": "A", "value": "1"}]})
    big = render_stats_card({"title": "X", "tiles": [
        {"label": l, "value": "1"} for l in "ABCDEF"]})
    assert _dims(big)[1] > _dims(small)[1]
