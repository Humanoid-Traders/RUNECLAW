"""
Chart render weirdness (2026-07-13) — from a live PENGUUSDT 1h render:

  1. Pattern zones were drawn with bare axhspan(lower, upper), which spans
     the FULL chart width — a wide wedge (upper≈range high, lower≈range
     low) tinted the entire upper half of the image edge-to-edge.
     Shading is now bounded to the pattern window (last ≤45 bars).
  2. The pattern name badge was hardcoded to x = n*0.05 (far left), so
     "Falling Wedge (forming)" floated over candles nowhere near the
     wedge. The badge now anchors to the pattern window start.
  3. Found while verifying: _send_single_photo was truncated since its
     first commit — it sent the photo but returned None, and its
     plain-caption retry did buf.seek(0) and nothing else. Every
     single-photo chart send reported failure, and an HTML caption error
     silently dropped the chart.
"""

from __future__ import annotations

import inspect

import pytest
from unittest.mock import AsyncMock, MagicMock

import bot.skills.chart_renderer as cr


# ── 1+2. pattern zones bounded to their window ───────────────────────
def test_pattern_shading_is_window_bounded():
    src = inspect.getsource(cr._pattern_zones_overlay)
    # Every pattern-zone axhspan carries the window bound (H&S, double,
    # triangle+wedge, flag, cup, rectangle = 7 sites) — a bare axhspan
    # spans the full chart width and repaints the smear.
    assert src.count("axhspan(") == src.count("xmin=_x0_frac") == 7
    # Badge anchors to the pattern window, not the chart's far left.
    assert "n * 0.05" not in src
    assert "_badge_x" in src


# ── 3. single-photo send reports the truth ───────────────────────────
def _bot(html_fails=False, plain_fails=False):
    bot = MagicMock()
    calls = {"n": 0}

    async def send_photo(chat_id, photo, caption, parse_mode=None):
        calls["n"] += 1
        if parse_mode == "HTML" and html_fails:
            raise RuntimeError("can't parse entities")
        if parse_mode is None and plain_fails:
            raise RuntimeError("network down")

    bot.send_photo = send_photo
    return bot, calls


@pytest.mark.asyncio
async def test_single_photo_success_returns_true():
    bot, calls = _bot()
    ok = await cr._send_single_photo(bot, 1, b"\x89PNG fake", "<b>BTC</b>")
    assert ok is True and calls["n"] == 1


@pytest.mark.asyncio
async def test_single_photo_html_failure_retries_plain():
    bot, calls = _bot(html_fails=True)
    ok = await cr._send_single_photo(bot, 1, b"\x89PNG fake", "<b>BTC</b>")
    assert ok is True and calls["n"] == 2   # HTML attempt + plain retry


@pytest.mark.asyncio
async def test_single_photo_total_failure_returns_false():
    bot, calls = _bot(html_fails=True, plain_fails=True)
    ok = await cr._send_single_photo(bot, 1, b"\x89PNG fake", "<b>BTC</b>")
    assert ok is False and calls["n"] == 2
