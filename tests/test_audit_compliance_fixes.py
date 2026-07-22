"""§4 compliance/secret-safety fixes surfaced by the readiness audit.

1. /classpf reads the OPERATOR's shared live-executor book and prints per-class
   dollar PnL. Without an auth guard, any Telegram caller (even unlinked) could
   read one account's dollars — a §4 cross-user dollar-disclosure. It must carry
   an @guard so the F-2 allowlist gates it like every sibling portfolio view.

2. F-15: when every chat LLM provider fails, the user-facing reply must NOT
   contain the raw provider exception (last_error / str(e)) — that string can
   embed a credential-bearing URL or an upstream body echoing an API key. The
   detail belongs in the audit log only.

Source-asserted: wiring the full TelegramHandler is heavy and unnecessary — the
regression we must lock is exactly the presence of the guard and the absence of
the raw error in the user-facing branch.
"""

import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent
       / "bot/skills/telegram_handler.py").read_text(encoding="utf-8")


def test_classpf_is_auth_guarded():
    # The @guard decorator must sit directly above the _cmd_classpf def.
    m = re.search(
        r'@guard\(\s*["\'][a-z_]+["\']\s*\)\s*\n\s*async def _cmd_classpf\b', SRC)
    assert m, "_cmd_classpf must carry an @guard(...) decorator (was ungated → §4 leak)"


def test_classpf_still_reads_the_live_book():
    # Guard added, behaviour otherwise unchanged — it still surfaces the class PnL.
    body = SRC[SRC.index("async def _cmd_classpf"):]
    body = body[:body.index("async def ", 10)]
    assert "closed_positions" in body


def test_f15_all_providers_failed_reply_omits_raw_error():
    # Isolate the "All providers failed" return block.
    idx = SRC.index("All providers failed")
    block = SRC[idx:idx + 700]
    # The user-facing _chat_ret in this block must not interpolate last_error /
    # str(e) into the reply string.
    ret_start = block.index("_chat_ret")
    ret_block = block[ret_start:ret_start + 300]
    assert "last_error" not in ret_block, "raw provider error must not reach the user reply (F-15)"
    assert "{error_str" not in ret_block and "str(e)" not in ret_block
    # A generic, safe message is still returned.
    assert "temporarily" in ret_block or "trouble thinking" in ret_block


def test_f15_detail_still_logged_for_operators():
    # The raw last_error must still be audited (operators keep the diagnostic).
    assert re.search(r'audit\([^)]*All chat LLM providers failed[^)]*last_error',
                     SRC.replace("\n", " ")) or \
        'f"All chat LLM providers failed. Last: {last_error}"' in SRC
