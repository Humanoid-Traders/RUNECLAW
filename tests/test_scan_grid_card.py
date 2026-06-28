"""
render_scan_grid_card: the breadth-grid + top-setups + sparkline scan card.
Display-only PNG renderer — assert it produces a valid PNG and degrades safely.
"""

import struct

from bot.formatters.signal_card import render_scan_grid_card


def _png_size(b: bytes):
    # PNG signature + IHDR width/height (big-endian u32 at offsets 16/20).
    assert b[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", b[16:24])
    return w, h


def _grid(n):
    return [
        {"sym": f"SYM{i}", "price": 10.0 + i, "change_pct": (i - n / 2) * 1.3,
         "rsi": 30 + i * 2, "score": (i % 10) / 10.0,
         "spark": [10.0 + i + (j % 5) * 0.2 for j in range(30)]}
        for i in range(n)
    ]


def test_renders_grid_only():
    png = render_scan_grid_card({
        "title": "US STOCK SCAN", "timestamp": "08:20 UTC",
        "grid": _grid(12),
        "summary": {"up": 7, "down": 5, "vol_usd": 1.18e8},
    })
    w, h = _png_size(png)
    assert w == 560 and h > 200


def test_renders_grid_and_setups():
    png = render_scan_grid_card({
        "title": "SCAN", "grid": _grid(8),
        "setups": [
            {"sym": "BTC", "direction": "LONG", "entry": 65000, "stop_loss": 63000,
             "take_profit": 70000, "rr": 2.5, "score": 0.82},
            {"sym": "ETH", "direction": "SHORT", "entry": 3400, "stop_loss": 3500,
             "take_profit": 3100, "rr": 3.0},
        ],
    })
    w, h = _png_size(png)
    assert w == 560 and h > 300


def test_empty_grid_returns_empty():
    assert render_scan_grid_card({"title": "X", "grid": []}) == b""


def test_missing_optionals_safe():
    # No rsi / score / spark / summary / setups — must still render.
    png = render_scan_grid_card({"grid": [{"sym": "A", "price": 1.0, "change_pct": -2.0}]})
    w, _ = _png_size(png)
    assert w == 560


def test_banner_increases_height():
    base = render_scan_grid_card({"grid": _grid(5)})
    with_banner = render_scan_grid_card({"grid": _grid(5), "banner": "⚠ Weekend"})
    assert _png_size(with_banner)[1] > _png_size(base)[1]
