"""
Checked skill dispatch (roadmap tech-debt item).

~30 call sites did `registry.get(name).execute(...)` with no Optional check, so
a mistyped/unregistered skill name raised
`AttributeError: 'NoneType' object has no attribute 'execute'` and crashed the
command. SkillRegistry.dispatch() resolves the skill first and returns a clean,
user-facing error (plus an audit record) when it is missing.
"""

import asyncio

from bot.skills.skill_registry import BaseSkill, SkillRegistry


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _EchoSkill(BaseSkill):
    name = "echo"
    description = "test skill"

    async def execute(self, engine, **kwargs) -> str:
        return f"ran:{kwargs.get('mode', 'default')}"


class TestDispatch:
    def test_runs_registered_skill_and_passes_kwargs(self):
        reg = SkillRegistry()
        reg.register(_EchoSkill())
        out = _run(reg.dispatch("echo", engine=None, mode="status"))
        assert out == "ran:status"

    def test_missing_skill_returns_clean_error_not_attributeerror(self):
        reg = SkillRegistry()
        # Would previously be registry.get("nope").execute(...) -> AttributeError.
        out = _run(reg.dispatch("nope", engine=None))
        assert isinstance(out, str)
        assert "nope" in out
        assert "unavailable" in out.lower()

    def test_get_still_returns_none_for_missing(self):
        # dispatch is the safe path; get() keeps its Optional contract.
        assert SkillRegistry().get("nope") is None


class TestCallSitesConverted:
    def test_no_unchecked_get_execute_remains(self):
        import pathlib
        import re
        root = pathlib.Path(__file__).resolve().parent.parent / "bot"
        pattern = re.compile(r'\.get\("[^"]*"\)\.execute\(')
        offenders = []
        for py in root.rglob("*.py"):
            for i, line in enumerate(py.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{py}:{i}")
        assert not offenders, "unchecked get(...).execute() still present:\n" + "\n".join(offenders)

    def test_handlers_use_dispatch(self):
        import pathlib
        th = (pathlib.Path(__file__).resolve().parent.parent
              / "bot" / "skills" / "telegram_handler.py").read_text()
        assert "registry.dispatch(" in th
