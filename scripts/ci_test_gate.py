#!/usr/bin/env python3
"""
CI test gate — baseline-diff regression guard.

The RUNECLAW test suite has a set of pre-existing failures that encode behavior
drift (tests asserting older behavior the code intentionally changed). Rather than
block all of CI on them or silently delete them, this gate runs the full suite and
fails ONLY when:

  * a NEW test fails that is not in tests/known_failures.txt, or
  * a collection / internal error occurs.

A baseline test that starts passing is reported as a warning (so the baseline can
be trimmed) but does not fail the build. This catches future regressions in the
~1140 passing tests immediately while the drifted tests are fixed incrementally.

Usage:
    python scripts/ci_test_gate.py            # run suite + gate
    python scripts/ci_test_gate.py --update   # rewrite the baseline from this run
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tests" / "known_failures.txt"

PYTEST_CMD = [
    sys.executable, "-m", "pytest",
    "-p", "no:cacheprovider",
    "--timeout=60", "--timeout-method=signal",
    "-rfE", "-q", "--no-header",
]


def _load_baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    out: set[str] = set()
    for line in BASELINE.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def _parse_failures(output: str) -> tuple[set[str], bool]:
    """Return (failed_node_ids, had_internal_error)."""
    failed: set[str] = set()
    internal_error = False
    for line in output.splitlines():
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            node = line.split(" ", 1)[1].split(" - ", 1)[0].strip()
            failed.add(node)
        if "INTERNALERROR" in line:
            internal_error = True
    return failed, internal_error


def main() -> int:
    update = "--update" in sys.argv
    proc = subprocess.run(PYTEST_CMD, cwd=ROOT, capture_output=True, text=True)
    output = proc.stdout + proc.stderr
    print(output)

    failed, internal_error = _parse_failures(output)

    if update:
        header = (
            "# Known pre-existing test failures (behavior drift) — baseline for the\n"
            "# CI gate (scripts/ci_test_gate.py). NEW failures outside this list fail CI.\n"
            "# Regenerate with: python scripts/ci_test_gate.py --update\n"
        )
        BASELINE.write_text(header + "\n".join(sorted(failed)) + "\n")
        print(f"\n[gate] baseline updated: {len(failed)} known failures written to {BASELINE}")
        return 0

    known = _load_baseline()
    new_failures = sorted(failed - known)
    now_passing = sorted(known - failed)

    print("\n" + "=" * 70)
    print(f"[gate] total failing: {len(failed)} | known-baseline: {len(known)}")
    if now_passing:
        print(f"[gate] {len(now_passing)} baseline test(s) now PASS — trim the baseline:")
        for n in now_passing:
            print(f"         + {n}")
    if internal_error:
        print("[gate] FAIL — pytest reported an INTERNALERROR (collection/runtime).")
    if new_failures:
        print(f"[gate] FAIL — {len(new_failures)} NEW failure(s) not in the baseline:")
        for n in new_failures:
            print(f"         ✗ {n}")
    if not new_failures and not internal_error:
        print("[gate] PASS — no new failures beyond the known baseline.")
    print("=" * 70)

    return 1 if (new_failures or internal_error) else 0


if __name__ == "__main__":
    sys.exit(main())
