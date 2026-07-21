"""TG-2b: /closeall requires a two-step confirm (audit-confirmed).

/emergency_stop already confirmed before flattening; /closeall market-closed
EVERY operator and per-user position IMMEDIATELY on a single admin tap — a
fat-finger with no undo. It now shows a confirm keyboard; the flatten runs only
from the closeall_confirm callback, which is permission-gated (halt) AND
re-checks admin. Verified by source assertion — the handler needs a full engine
+ Telegram Update to run.
"""

from __future__ import annotations

import inspect

from bot.skills import telegram_handler as th


def test_closeall_shows_a_confirm_and_does_not_flatten_immediately():
    src = inspect.getsource(th.TelegramHandler._cmd_close_all)
    # It renders a confirm keyboard …
    assert 'callback_data="closeall_confirm"' in src
    assert 'callback_data="closeall_cancel"' in src
    assert "Flatten ALL open positions on EVERY account?" in src
    # … and does NOT call the flatten directly anymore.
    assert "flatten_all_positions" not in src


def test_the_actual_flatten_lives_behind_the_confirm():
    src = inspect.getsource(th.TelegramHandler._flatten_all_accounts)
    assert 'flatten_all_positions(reason="admin_closeall")' in src


def test_confirm_callback_is_permission_gated_and_admin_rechecked():
    src = inspect.getsource(th.TelegramHandler)
    # Registered as a destructive callback requiring the halt permission.
    assert '"closeall_confirm": "halt"' in src
    # The confirm branch re-checks admin before flattening, and cancel is inert.
    assert 'if data == "closeall_confirm":' in src
    assert 'if data == "closeall_cancel":' in src
    confirm_at = src.index('if data == "closeall_confirm":')
    branch = src[confirm_at:confirm_at + 400]
    assert "_is_admin(update)" in branch
    assert "_flatten_all_accounts(update)" in branch
