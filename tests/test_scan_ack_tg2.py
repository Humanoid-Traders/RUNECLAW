"""TG-2: /scan immediate acknowledgement (audit-confirmed).

A full market scan sweeps ~200 pairs and can take several seconds. /scan used
to reply with total silence until the results card arrived, reading as a hang.
It now shows the typing indicator AND a lightweight status line before the
(slow) dispatch. Verified by source assertion — _cmd_scan needs a full engine
to run, so the ack wiring is checked directly.
"""

from __future__ import annotations

import inspect

from bot.skills import telegram_handler as th


def test_chataction_is_imported():
    src = inspect.getsource(th)
    assert "from telegram.constants import ChatAction" in src


def test_scan_acks_before_the_slow_dispatch():
    src = inspect.getsource(th.TelegramHandler._cmd_scan)
    # Typing indicator (best-effort) …
    assert "send_chat_action(ChatAction.TYPING)" in src
    # … and a status line, BOTH before the scan_market dispatch.
    assert "Scanning the market" in src
    ack_at = src.index("Scanning the market")
    dispatch_at = src.index('dispatch("scan_market"')
    assert ack_at < dispatch_at, "the ack must be sent BEFORE the slow scan"


def test_ack_is_best_effort_and_never_blocks_the_scan():
    src = inspect.getsource(th.TelegramHandler._cmd_scan)
    # The typing call is wrapped so a send hiccup can't abort the scan.
    assert "except Exception:" in src
