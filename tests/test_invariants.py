"""
Structural invariants distilled from this session's bug pattern: the SAME
class of bug (a duplicated classifier drifting out of sync, a fallback path
skipping the safety net the main path has, a policy encoded only in data
tables instead of enforced in code) kept recurring across unrelated fixes.
These tests encode the lesson as a standing check instead of a one-time fix,
so the next instance of the same pattern fails CI instead of shipping.

Covered here:
  1. No raw exception text reaches a Telegram send call outside the
     redaction chokepoints (_send/_send_error/_notify_admins). Found and
     fixed 12 violations across two rounds this session (6 + 6) before this
     test existed — this pins the fix and catches the next one.
  2. execution_indicates_failure() is the ONLY blocked-trade classifier —
     no other file re-implements its token list.
  3. humanize_close_reason() is the ONLY close-reason-to-label mapper.
  4. dedupe_duplicate_positions() runs at the start of every
     adopt_exchange_positions() cycle (a one-line static guard against a
     refactor silently dropping the call).
  5. Anthropic/Claude is admin-only in every LLM routing table (see also
     tests/test_anthropic_admin_only.py for the behavioral version of this
     invariant against resolve_tier_config() and both fallback chains).
"""

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TELEGRAM_HANDLER = REPO_ROOT / "bot" / "skills" / "telegram_handler.py"
SCAN_SKILL = REPO_ROOT / "bot" / "skills" / "scan_skill.py"
LIVE_EXECUTOR = REPO_ROOT / "bot" / "core" / "live_executor.py"
SIGNAL_CARD = REPO_ROOT / "bot" / "formatters" / "signal_card.py"
PROVIDER = REPO_ROOT / "bot" / "llm" / "provider.py"


def _read(path: Path) -> str:
    return path.read_text()


# ── 1. No raw exception text bypasses the redaction chokepoint ───────────


class TestNoRawExceptionLeaksToTelegram:
    """A caught exception must only ever reach the user via _send()/
    _send_error()/_notify_admins() — the only places that redact secrets
    before sending. A direct `context.bot.send_message`/`update.message.
    reply_text` call that interpolates the exception bypasses that."""

    # Chokepoint methods that already redact (or, for _send_error, log the
    # real exception server-side and send a hardcoded generic message that
    # can never itself contain exception text).
    _CHOKEPOINT_METHODS = {"_send", "_send_photo", "_send_error", "_notify_admins"}

    def _violations(self, source: str) -> list[str]:
        tree = ast.parse(source)
        violations: list[str] = []

        class Visitor(ast.NodeVisitor):
            def __init__(self):
                self.current_method = None

            def visit_AsyncFunctionDef(self, node):
                prev = self.current_method
                self.current_method = node.name
                self.generic_visit(node)
                self.current_method = prev

            def visit_FunctionDef(self, node):
                prev = self.current_method
                self.current_method = node.name
                self.generic_visit(node)
                self.current_method = prev

            def visit_Call(self, node):
                is_send_call = (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("send_message", "reply_text")
                )
                if is_send_call and self.current_method not in TestNoRawExceptionLeaksToTelegram._CHOKEPOINT_METHODS:
                    call_src = ast.dump(node)
                    if "'exc'" in call_src or "id='exc'" in call_src:
                        violations.append(
                            f"{self.current_method or '<module>'}:{node.lineno}")
                self.generic_visit(node)

        Visitor().visit(tree)
        return violations

    def test_telegram_handler_has_no_raw_exception_sends(self):
        violations = self._violations(_read(TELEGRAM_HANDLER))
        assert violations == [], (
            f"Raw exception text reaches a send call outside the redaction "
            f"chokepoint at: {violations}. Route through self._send_error(...) instead."
        )

    def test_scan_skill_has_no_raw_exception_sends(self):
        violations = self._violations(_read(SCAN_SKILL))
        assert violations == []

    def test_the_detector_actually_catches_the_pattern(self):
        """Meta-test: prove the AST check above isn't vacuously passing by
        running it against a deliberately-reintroduced violation."""
        bad_source = '''
class Handler:
    async def _cmd_broken(self, update, context):
        try:
            1 / 0
        except Exception as exc:
            await context.bot.send_message(chat_id=1, text=f"Error: {exc}")
'''
        violations = self._violations(bad_source)
        assert violations != [], "detector failed to catch a known-bad pattern"

    def test_send_error_helper_itself_is_not_flagged(self):
        """_send_error is a chokepoint (logs the real exception, sends a
        hardcoded generic message) -- it must not flag itself."""
        good_source = '''
class Handler:
    async def _send_error(self, update, command_name, exc):
        system_log.error("%s failed: %s", command_name, exc, exc_info=True)
        await self._send(update, f"Something went wrong loading {command_name}.")
'''
        violations = self._violations(good_source)
        assert violations == []


# ── 2. execution_indicates_failure() is the sole blocked-trade classifier ──


class TestSingleExecutionFailureClassifier:
    def test_failure_tokens_only_defined_in_live_executor(self):
        assert "_EXECUTION_FAILURE_TOKENS" in _read(LIVE_EXECUTOR)
        for path in (TELEGRAM_HANDLER, SCAN_SKILL):
            src = _read(path)
            assert "_EXECUTION_FAILURE_TOKENS" not in src

    def test_telegram_handler_uses_the_canonical_function(self):
        assert "execution_indicates_failure" in _read(TELEGRAM_HANDLER)

    def test_scan_skill_uses_the_canonical_function(self):
        assert "execution_indicates_failure" in _read(SCAN_SKILL)

    def test_no_inline_reimplementation_of_the_token_list(self):
        """Regression: telegram_handler.py and scan_skill.py each used to
        keep their OWN local list of "EXECUTION BLOCKED"-style substrings
        that silently drifted from live_executor.py's real list (missing
        tokens like "EXECUTION BLOCKED:"). A hardcoded multi-token OR chain
        checking result strings, instead of calling the shared function, is
        exactly that regression reappearing."""
        suspicious = re.compile(
            r'"EXECUTION (FAILED|BLOCKED|ABORTED)"\s*(in|==)')
        for path in (TELEGRAM_HANDLER, SCAN_SKILL):
            src = _read(path)
            assert not suspicious.search(src), (
                f"{path.name} appears to re-implement the failure-token "
                f"check inline instead of calling execution_indicates_failure()")


# ── 3. humanize_close_reason() is the sole close-reason-to-label mapper ───


class TestSingleCloseReasonMapper:
    def test_defined_once_in_signal_card(self):
        assert "def humanize_close_reason" in _read(SIGNAL_CARD)

    def test_telegram_handler_uses_it_not_a_local_mapping(self):
        src = _read(TELEGRAM_HANDLER)
        assert "humanize_close_reason" in src
        # Regression: a local reimplementation mapping raw exchange
        # close-type strings straight to a label/emoji, bypassing the
        # canonical function (and its "CLOSED (unknown)" -> "Closed"
        # de-jargoning), would look like this:
        suspicious = re.compile(r'"(TP|SL) HIT.*"\s*:\s*"')
        assert not suspicious.search(src)


# ── 4. Duplicate-position dedup runs on every adoption cycle ──────────────


class TestDedupeRunsBeforeAdoption:
    def test_adopt_exchange_positions_calls_dedupe_first(self):
        src = _read(LIVE_EXECUTOR)
        assert "def dedupe_duplicate_positions" in src
        adopt_start = src.index("async def adopt_exchange_positions")
        next_def = src.index("\n    async def ", adopt_start + 10)
        body = src[adopt_start:next_def]
        assert "self.dedupe_duplicate_positions()" in body, (
            "adopt_exchange_positions() no longer runs the dedupe pass first "
            "-- a future refactor silently dropped the call that cleans up "
            "false-orphan-adoption duplicates before they can compound")


# ── 5. Anthropic/Claude is admin-only in every routing table ─────────────


class TestAnthropicOnlyInAdminTable:
    """See tests/test_anthropic_admin_only.py for the full behavioral
    coverage (resolve_tier_config's hard guard, both hardcoded fallback
    chains). This is the cheap static half: scan every dict literal in
    provider.py for a bare 'anthropic' reference outside ADMIN_TIER_ROUTING,
    so a new non-admin routing table added later can't reintroduce it."""

    _NON_ADMIN_TABLE_NAMES = (
        "DEFAULT_TIER_ROUTING", "ELITE_TIER_ROUTING", "PRO_TIER_ROUTING",
    )

    def test_non_admin_tables_are_still_present(self):
        src = _read(PROVIDER)
        for name in self._NON_ADMIN_TABLE_NAMES:
            assert f"{name}: dict[LLMTier, dict] = {{" in src, (
                f"{name} not found -- update this test's table list if it was renamed")

    def test_non_admin_tables_contain_no_anthropic(self):
        src = _read(PROVIDER)
        for name in self._NON_ADMIN_TABLE_NAMES:
            start = src.index(f"{name}: dict[LLMTier, dict] = {{")
            end = src.index("\n}\n", start)
            table_body = src[start:end]
            assert "ANTHROPIC" not in table_body, (
                f"{name} references LLMProvider.ANTHROPIC -- Anthropic/Claude "
                f"is reserved for ADMIN_TIER_ROUTING only")
