"""Run a snippet in a FRESH interpreter with env overrides — the safe way to
test import-time env reads.

`importlib.reload` in-process is the alternative, and it is poison: reloading
a module re-executes it in the same namespace, replacing every class/function
object. Test modules that imported those objects at collection time keep the
old ones, so their monkeypatch.setattr targets silently stop matching what the
code under test resolves — which is exactly how a single reload-based test
broke 30 unrelated tests across the suite (2026-07 full-suite audit). A
subprocess exercises the real import-time path with zero shared-state damage.
"""
import os
import subprocess
import sys


def run_py(code: str, env_overrides: dict[str, str] | None = None,
           env_removals: tuple[str, ...] = ()) -> str:
    """Execute `code` with `sys.executable -c` and return stripped stdout.

    Raises on nonzero exit with stderr attached, so assertion failures inside
    the snippet surface as readable test failures.
    """
    env = dict(os.environ)
    for k in env_removals:
        env.pop(k, None)
    env.update(env_overrides or {})
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, env=env, timeout=120,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"subprocess check failed (exit {proc.returncode}):\n{proc.stderr}")
    return proc.stdout.strip()
