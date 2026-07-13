"""
/start "Open positions: 0" with a live position (2026-07-13, LTC).

The /start card displays `_filled_count` (locally-tracked open records),
and the handler already had an exchange fallback written for exactly the
orphan case ("opened but lost from local state") — but the fallback
corrected `open_pos`, a variable the card never renders. So it fired,
found the position on the exchange, and fixed a number nobody saw.

Also latent: the PAPER branch never defined _filled_count/_pending_count
while the template references both — a NameError waiting for
simulation_mode.
"""

from __future__ import annotations

import inspect


def test_exchange_fallback_corrects_the_displayed_count():
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_start)
    # The fallback now gates on and assigns the DISPLAYED variable.
    assert "if _filled_count == 0 and executor:" in src
    assert "_filled_count = len(_ex_open)" in src
    # The template renders _filled_count.
    assert "filled=_filled_count" in src


def test_counts_defined_for_paper_branch():
    from bot.skills.telegram_handler import TelegramHandler
    src = inspect.getsource(TelegramHandler._cmd_start)
    # Initialized before the LIVE/PAPER branch so neither path NameErrors.
    assert src.index("_filled_count = 0") < src.index('if mode_str == "LIVE":')
    assert src.index("_pending_count = 0") < src.index('if mode_str == "LIVE":')
    # Paper branch mirrors its portfolio count into the displayed variable.
    assert "_filled_count = state.open_positions" in src
