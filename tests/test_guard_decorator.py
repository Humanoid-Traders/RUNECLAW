"""
The @guard decorator must be behaviourally identical to the copy-pasted
`if not await self._guard(update, "..."): return` prelude it replaces: run the
gate first, pass the permission string through, short-circuit (no body, no
return value) when the gate fails, and run the body when it passes.
"""

import asyncio

from bot.skills.telegram_handler import guard


class _FakeHandler:
    def __init__(self, allow):
        self._allow = allow
        self.checked_command = None
        self.body_ran = False

    async def _guard(self, update, command=""):
        self.checked_command = command
        return self._allow

    @guard("trade")
    async def cmd(self, update, ctx):
        self.body_ran = True
        return "BODY"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_runs_body_when_gate_allows():
    h = _FakeHandler(allow=True)
    result = _run(h.cmd("update", "ctx"))
    assert h.checked_command == "trade"   # permission string passed through
    assert h.body_ran is True
    assert result == "BODY"


def test_short_circuits_when_gate_denies():
    h = _FakeHandler(allow=False)
    result = _run(h.cmd("update", "ctx"))
    assert h.checked_command == "trade"   # gate still ran
    assert h.body_ran is False            # body did NOT run
    assert result is None                 # early return, like the old prelude


def test_preserves_name_via_wraps():
    assert _FakeHandler.cmd.__name__ == "cmd"
