"""NEWS-1c: the /gateway/news web surface is registered, read-only, advisory.

The handler needs a live aiohttp request + engine to run, so the contract is
checked by source assertion (same approach as the other gateway handlers).
"""

from __future__ import annotations

import inspect

from bot.web import user_gateway as ug


def test_news_gateway_route_is_registered():
    src = inspect.getsource(ug.build_gateway)
    assert 'add_get("/news", handle_news)' in src


def test_news_handler_is_read_only_and_advisory():
    src = inspect.getsource(ug.handle_news)
    # Reuses the gated radar and the user's held positions.
    assert "NewsRadar" in src
    assert "open_positions" in src
    assert "standdown" in src
    # Explicitly read-only; nothing here trades.
    assert '"read_only": True' in src
    assert "moves, sizes, or blocks a trade" in src
    # Refresh only runs when the flag is enabled.
    assert "if enabled:" in src and "radar.refresh" in src
